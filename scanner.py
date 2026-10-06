"""
Core engine for the offline "Look Scanned" clone.
PDF in -> realistic scanned PDF (or ZIP of page images) out.
"""
from __future__ import annotations

import glob
import io
import math
import os
import random
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pikepdf
import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
PAPER_SIZES = {  # points (1/72 inch), portrait
    "Auto (original size)": None,
    "A3": (841.89, 1190.55),
    "A4": (595.276, 841.89),
    "A5": (419.53, 595.276),
    "Letter": (612, 792),
    "Legal": (612, 1008),
}


@dataclass
class Effects:
    colorspace: str = "Grayscale"      # Color | Grayscale | Black & White
    border: bool = False
    rotate: float = 1.0                # degrees
    rotate_variance: float = 0.5       # +/- degrees, random per page
    brightness: float = 100            # percent
    contrast: float = 100              # percent
    blur: float = 30                   # percent
    noise: float = 10                  # percent
    yellowish: float = 0               # percent
    ppi: int = 150


@dataclass
class Stamp:
    image: Image.Image                 # RGBA
    x: float = 75.0                    # centre, % of sheet width
    y: float = 85.0                    # centre, % of sheet height
    width: float = 25.0                # % of sheet width
    rotation: float = 0.0              # degrees (counter-clockwise)
    opacity: float = 100.0             # percent
    pages: str = "all"                 # all | first | last | 1,3-5


@dataclass
class Watermark:
    enabled: bool = False
    text: str = "CONFIDENTIAL"
    image: Optional[Image.Image] = None  # used instead of text when set
    size: float = 8.0                  # text height / image width, % of sheet width
    opacity: float = 20.0
    angle: float = 45.0
    color: str = "#808080"
    tiled: bool = False
    spacing: float = 100.0             # % extra gap when tiled


@dataclass
class Meta:
    enabled: bool = False
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    producer: str = ""
    creator: str = ""
    creation_date: Optional[datetime] = None
    modification_date: Optional[datetime] = None


@dataclass
class Options:
    effects: Effects = field(default_factory=Effects)
    paper: str = "Auto (original size)"
    stamps: list = field(default_factory=list)
    watermark: Watermark = field(default_factory=Watermark)
    meta: Meta = field(default_factory=Meta)
    pdfa: str = "Off"                  # Off | PDF/A-1b | PDF/A-2b
    output_format: str = "PDF"         # PDF | Images (ZIP)
    image_format: str = "JPEG"         # JPEG | PNG (only for ZIP output)
    jpeg_quality: int = 85
    keep_text: bool = False
    suffix: str = "-scan"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
]

SIGNATURE_FONTS = {  # label -> Windows font file
    "Ink Free": "Inkfree.ttf",
    "Segoe Script": "segoesc.ttf",
    "Lucida Handwriting": "LHANDW.TTF",
    "Brush Script MT": "BRUSHSCI.TTF",
    "Freestyle Script": "FREESCPT.TTF",
    "Mistral": "MISTRAL.TTF",
}


def find_font(names: list[str]) -> Optional[str]:
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    return None


def load_font(size: int, bold: bool = False, file: Optional[str] = None) -> ImageFont.ImageFont:
    cands = [file] if file else []
    cands += (["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if bold
              else ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    p = find_font([c for c in cands if c])
    try:
        return ImageFont.truetype(p, size) if p else ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default(size)


def available_signature_fonts() -> list[str]:
    return [k for k, v in SIGNATURE_FONTS.items() if find_font([v])]


def parse_pages(spec: str, n: int) -> set[int]:
    """'all', 'first', 'last', '1,3-5' -> set of 0-based page indexes."""
    spec = (spec or "all").strip().lower()
    if spec in ("all", ""):
        return set(range(n))
    if spec == "first":
        return {0}
    if spec == "last":
        return {n - 1}
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                out.update(range(int(a) - 1, min(int(b), n)))
        elif part.isdigit():
            out.add(int(part) - 1)
    return {i for i in out if 0 <= i < n}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


# --------------------------------------------------------------------------- #
# Stamp / signature builders
# --------------------------------------------------------------------------- #
def remove_white_background(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Make near-white pixels transparent (for scanned signatures)."""
    img = img.convert("RGBA")
    arr = np.array(img).astype(np.float32)
    lum = arr[..., :3].mean(axis=2)
    lo = max(threshold - 60, 0)
    alpha = np.clip((threshold - lum) / max(threshold - lo, 1), 0, 1) * 255
    arr[..., 3] = np.minimum(arr[..., 3], alpha)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def make_typed_signature(text: str, font_label: str, color: str = "#1a237e") -> Image.Image:
    font_file = SIGNATURE_FONTS.get(font_label)
    font = load_font(160, file=font_file)
    tmp = Image.new("RGBA", (10, 10))
    l, t, r, b = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (r - l + 60, b - t + 60), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((30 - l, 30 - t), text, font=font, fill=hex_to_rgb(color) + (255,))
    return img


def make_text_stamp(text: str, color: str = "#c62828", border: bool = True, sub: str = "") -> Image.Image:
    """Boxed rubber-stamp look, e.g. APPROVED / PAID / RECEIVED."""
    font = load_font(150, bold=True)
    small = load_font(60, bold=True)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    l, t, r, b = tmp.textbbox((0, 0), text, font=font)
    w, h = r - l, b - t
    sw = sh = 0
    if sub:
        a, bb, c, d = tmp.textbbox((0, 0), sub, font=small)
        sw, sh = c - a, d - bb
    pad = 50
    W = max(w, sw) + pad * 2
    H = h + (sh + 30 if sub else 0) + pad * 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = hex_to_rgb(color) + (255,)
    d.text(((W - w) / 2 - l, pad - t), text, font=font, fill=col)
    if sub:
        d.text(((W - sw) / 2, pad + h + 30 - bb), sub, font=small, fill=col)
    if border:
        d.rounded_rectangle((8, 8, W - 9, H - 9), radius=18, outline=col, width=12)
    return img


# --------------------------------------------------------------------------- #
# Rendering / compositing
# --------------------------------------------------------------------------- #
def sheet_geometry(page_rect, paper: str):
    """Return (sheet_w_pt, sheet_h_pt, scale, off_x_pt, off_y_pt)."""
    pw, ph = page_rect.width, page_rect.height
    size = PAPER_SIZES.get(paper)
    if size is None:
        return pw, ph, 1.0, 0.0, 0.0
    sw, sh = size
    if (pw > ph) != (sw > sh):          # match orientation
        sw, sh = sh, sw
    scale = min(sw / pw, sh / ph)
    return sw, sh, scale, (sw - pw * scale) / 2, (sh - ph * scale) / 2


def render_sheet(page: pymupdf.Page, ppi: int, paper: str) -> tuple[Image.Image, tuple]:
    geo = sheet_geometry(page.rect, paper)
    sw, sh, scale, ox, oy = geo
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ppi / 72 * scale, ppi / 72 * scale), alpha=False)
    pg = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    W, H = round(sw * ppi / 72), round(sh * ppi / 72)
    if (W, H) == pg.size:
        return pg, geo
    sheet = Image.new("RGB", (W, H), "white")
    sheet.paste(pg, (round(ox * ppi / 72), round(oy * ppi / 72)))
    return sheet, geo


def _alpha_scale(img: Image.Image, opacity: float) -> Image.Image:
    img = img.convert("RGBA")
    a = img.getchannel("A").point(lambda v: int(v * max(0.0, min(opacity, 100.0)) / 100))
    img.putalpha(a)
    return img


def _rotate_rgba(img: Image.Image, angle: float) -> Image.Image:
    return img.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0)) if angle else img


def apply_watermark(sheet: Image.Image, wm: Watermark) -> Image.Image:
    if not wm.enabled or (not wm.text and wm.image is None):
        return sheet
    W, H = sheet.size
    if wm.image is not None:
        w = max(int(W * wm.size / 100), 8)
        src = wm.image.convert("RGBA")
        mark = src.resize((w, max(int(src.height * w / src.width), 1)), Image.LANCZOS)
    else:
        font = load_font(max(int(W * wm.size / 100), 8), bold=True)
        tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        l, t, r, b = tmp.textbbox((0, 0), wm.text, font=font)
        mark = Image.new("RGBA", (r - l + 8, b - t + 8), (0, 0, 0, 0))
        ImageDraw.Draw(mark).text((4 - l, 4 - t), wm.text, font=font, fill=hex_to_rgb(wm.color) + (255,))
    mark = _alpha_scale(_rotate_rgba(mark, wm.angle), wm.opacity)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if wm.tiled:
        gx = int(mark.width * (1 + wm.spacing / 100))
        gy = int(mark.height * (1 + wm.spacing / 100))
        row = 0
        for y in range(-mark.height, H + mark.height, max(gy, 1)):
            shift = (gx // 2) if row % 2 else 0
            for x in range(-mark.width + shift, W + mark.width, max(gx, 1)):
                layer.paste(mark, (x, y), mark)
            row += 1
    else:
        layer.paste(mark, ((W - mark.width) // 2, (H - mark.height) // 2), mark)
    return Image.alpha_composite(sheet.convert("RGBA"), layer).convert("RGB")


def apply_stamps(sheet: Image.Image, stamps: list[Stamp], page_index: int, n_pages: int) -> Image.Image:
    if not stamps:
        return sheet
    base = sheet.convert("RGBA")
    W, H = base.size
    for s in stamps:
        if page_index not in parse_pages(s.pages, n_pages):
            continue
        w = max(int(W * s.width / 100), 4)
        img = s.image.convert("RGBA")
        img = img.resize((w, max(int(img.height * w / img.width), 1)), Image.LANCZOS)
        img = _alpha_scale(_rotate_rgba(img, s.rotation), s.opacity)
        cx, cy = int(W * s.x / 100), int(H * s.y / 100)
        base.paste(img, (cx - img.width // 2, cy - img.height // 2), img)
    return base.convert("RGB")


def apply_effects(sheet: Image.Image, fx: Effects, rng: random.Random) -> tuple[Image.Image, float]:
    """Returns (image, rotation_angle_used)."""
    img = sheet.convert("RGB")
    W, H = img.size
    k = fx.ppi / 150.0

    if fx.border:
        bw = max(int(3 * k), 1)
        ImageDraw.Draw(img).rectangle((0, 0, W - 1, H - 1), outline=(60, 60, 60), width=bw)

    angle = fx.rotate + (rng.uniform(-fx.rotate_variance, fx.rotate_variance) if fx.rotate_variance else 0.0)
    if abs(angle) > 1e-4:
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))

    if fx.brightness != 100:
        img = ImageEnhance.Brightness(img).enhance(fx.brightness / 100)
    if fx.contrast != 100:
        img = ImageEnhance.Contrast(img).enhance(fx.contrast / 100)

    if fx.yellowish > 0:
        t = fx.yellowish / 100
        arr = np.asarray(img).astype(np.float32)
        tint = np.array([1.0, 1.0 - 0.10 * t, 1.0 - 0.38 * t], dtype=np.float32)
        img = Image.fromarray(np.clip(arr * tint, 0, 255).astype(np.uint8))

    if fx.blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=fx.blur / 100 * 2.0 * k))

    if fx.noise > 0:
        arr = np.asarray(img).astype(np.float32)
        nrng = np.random.default_rng(rng.getrandbits(32))
        sigma = fx.noise / 100 * 40
        lum = nrng.normal(0, sigma, size=arr.shape[:2])[..., None]
        chroma = nrng.normal(0, sigma * 0.25, size=arr.shape)
        img = Image.fromarray(np.clip(arr + lum + chroma, 0, 255).astype(np.uint8))

    if fx.colorspace == "Grayscale":
        img = ImageOps.grayscale(img)
    elif fx.colorspace == "Black & White":
        img = ImageOps.grayscale(img).point(lambda v: 255 if v >= 128 else 0).convert("1")
    return img, angle


def scan_page_image(page, index, n_pages, opts: Options, ppi: int, rng: random.Random):
    """Full per-page pipeline -> (final PIL image, geometry, angle)."""
    fx = opts.effects if ppi == opts.effects.ppi else Effects(**{**opts.effects.__dict__, "ppi": ppi})
    sheet, geo = render_sheet(page, ppi, opts.paper)
    sheet = apply_watermark(sheet, opts.watermark)
    sheet = apply_stamps(sheet, opts.stamps, index, n_pages)
    img, angle = apply_effects(sheet, fx, rng)
    return img, geo, angle


# --------------------------------------------------------------------------- #
# Invisible text layer
# --------------------------------------------------------------------------- #
def add_text_layer(out_page: pymupdf.Page, src_page: pymupdf.Page, geo, angle: float, sheet_px: tuple[int, int]):
    if src_page.rotation:
        return
    sw, sh, scale, ox, oy = geo
    th = math.radians(angle)
    cos, sin = math.cos(th), math.sin(th)
    cx, cy = sw / 2, sh / 2
    mat = pymupdf.Matrix(cos, -sin, sin, cos, 0, 0)
    for x0, y0, x1, y1, word, *_ in src_page.get_text("words"):
        if not word.strip() or any(ord(c) > 255 for c in word):
            continue  # built-in Helvetica only covers Latin-1
        bx, by = x0 * scale + ox, (y1 - (y1 - y0) * 0.22) * scale + oy   # baseline-left, sheet pts
        dx, dy = bx - cx, by - cy
        px, py = cx + dx * cos + dy * sin, cy - dx * sin + dy * cos
        natural = pymupdf.get_text_length(word, fontname="helv", fontsize=1)
        fs = ((x1 - x0) * scale / natural) if natural else (y1 - y0) * scale * 0.8
        fs = max(min(fs, (y1 - y0) * scale * 3), 1)
        out_page.insert_text(pymupdf.Point(px, py), word, fontname="helv", fontsize=fs,
                             render_mode=3, morph=(pymupdf.Point(px, py), mat))


# --------------------------------------------------------------------------- #
# Metadata + PDF/A
# --------------------------------------------------------------------------- #
def _pdf_date(dt: datetime) -> str:
    dt = dt.astimezone()
    off = dt.strftime("%z") or "+0000"
    return f"D:{dt:%Y%m%d%H%M%S}{off[:3]}'{off[3:]}'"


def write_metadata(path: str, meta: Meta) -> None:
    now = datetime.now()
    cd = meta.creation_date or now
    md = meta.modification_date or now
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        info = pdf.docinfo
        for key, val in (("/Title", meta.title), ("/Author", meta.author), ("/Subject", meta.subject),
                         ("/Keywords", meta.keywords), ("/Producer", meta.producer), ("/Creator", meta.creator)):
            if key in info:
                del info[key]
            if val:
                info[key] = pikepdf.String(val)
        info["/CreationDate"] = pikepdf.String(_pdf_date(cd))
        info["/ModDate"] = pikepdf.String(_pdf_date(md))

        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
            def put(k, v):
                if v:
                    xmp[k] = v
                elif k in xmp:
                    del xmp[k]
            put("dc:title", meta.title)
            put("dc:creator", [meta.author] if meta.author else "")
            put("dc:description", meta.subject)
            put("pdf:Keywords", meta.keywords)
            put("pdf:Producer", meta.producer)
            put("xmp:CreatorTool", meta.creator)
            xmp["xmp:CreateDate"] = cd.astimezone().isoformat(timespec="seconds")
            xmp["xmp:ModifyDate"] = md.astimezone().isoformat(timespec="seconds")
            xmp["xmp:MetadataDate"] = md.astimezone().isoformat(timespec="seconds")
        pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable)


def find_ghostscript() -> Optional[str]:
    for name in ("gswin64c", "gswin32c", "gs"):
        p = shutil.which(name)
        if p:
            return p
    for pattern in (r"C:\Program Files\gs\gs*\bin\gswin64c.exe", r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe"):
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    return None


def _find_icc(gs: str) -> Optional[str]:
    root = Path(gs).resolve().parent.parent
    pats = [str(root / "iccprofiles" / "default_rgb.icc"), str(root / "iccprofiles" / "srgb.icc"),
            "/usr/share/ghostscript/*/iccprofiles/default_rgb.icc", "/usr/share/color/icc/**/sRGB*.icc"]
    for p in pats:
        hits = glob.glob(p, recursive=True)
        if hits:
            return hits[0]
    return None


def convert_pdfa(src: str, dst: str, level: str) -> None:
    gs = find_ghostscript()
    if not gs:
        raise RuntimeError("Ghostscript not found. Install it from ghostscript.com/releases or turn PDF/A off.")
    icc = _find_icc(gs)
    if not icc:
        raise RuntimeError("Ghostscript ICC profile (default_rgb.icc) not found.")
    part = "1" if level.startswith("PDF/A-1") else "2"
    with tempfile.TemporaryDirectory() as td:
        defps = os.path.join(td, "PDFA_def.ps")
        Path(defps).write_text(
            "%!\n"
            f"/ICCProfile ({icc.replace(chr(92), '/')}) def\n"
            "[/_objdef {icc_PDFA} /type /stream /OBJ pdfmark\n"
            "[{icc_PDFA} <</N 3>> /PUT pdfmark\n"
            "[{icc_PDFA} ICCProfile (r) file /PUT pdfmark\n"
            "[/_objdef {OutputIntent_PDFA} /type /dict /OBJ pdfmark\n"
            "[{OutputIntent_PDFA} <</Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {icc_PDFA} "
            "/OutputConditionIdentifier (sRGB) /Info (sRGB)>> /PUT pdfmark\n"
            "[{Catalog} <</OutputIntents [ {OutputIntent_PDFA} ]>> /PUT pdfmark\n",
            encoding="utf-8")
        cmd = [gs, f"-dPDFA={part}", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dNOSAFER", "-dNOOUTERSAVE",
               "-sColorConversionStrategy=RGB", "-sDEVICE=pdfwrite", "-dPDFACompatibilityPolicy=1",
               f"-sOutputFile={dst}", defps, src]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dst):
            raise RuntimeError(f"Ghostscript failed: {r.stderr or r.stdout}")


# --------------------------------------------------------------------------- #
# Main entry points
# --------------------------------------------------------------------------- #
def _encode(img: Image.Image, fmt: str, quality: int) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if img.mode == "1" or fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "png"
    img.save(buf, "JPEG", quality=quality, dpi=(0, 0))
    return buf.getvalue(), "jpg"


def output_name(filename: str, suffix: str, ext: str) -> str:
    return f"{Path(filename).stem}{suffix}.{ext}"


def process_pdf(data: bytes, filename: str, opts: Options,
                progress: Optional[Callable[[float], None]] = None,
                seed: Optional[int] = None) -> tuple[bytes, str, str]:
    """Returns (file_bytes, output_filename, mime)."""
    rng = random.Random(seed)
    src = pymupdf.open(stream=data, filetype="pdf")
    n = len(src)
    ppi = opts.effects.ppi

    if opts.output_format.startswith("Images"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for i in range(n):
                img, _, _ = scan_page_image(src[i], i, n, opts, ppi, rng)
                b, ext = _encode(img, opts.image_format, opts.jpeg_quality)
                z.writestr(f"{Path(filename).stem}{opts.suffix}-page-{i + 1:03d}.{ext}", b)
                if progress:
                    progress((i + 1) / n)
        return buf.getvalue(), output_name(filename, opts.suffix, "zip"), "application/zip"

    out = pymupdf.open()
    for i in range(n):
        sp = src[i]
        img, geo, angle = scan_page_image(sp, i, n, opts, ppi, rng)
        b, _ = _encode(img, "JPEG", opts.jpeg_quality)
        op = out.new_page(width=geo[0], height=geo[1])
        op.insert_image(op.rect, stream=b)
        if opts.keep_text:
            add_text_layer(op, sp, geo, angle, img.size)
        if progress:
            progress((i + 1) / n)
    src.close()

    with tempfile.TemporaryDirectory() as td:
        p1, p2 = os.path.join(td, "a.pdf"), os.path.join(td, "b.pdf")
        out.save(p1, garbage=3, deflate=True)
        out.close()
        final = p1
        if opts.pdfa != "Off":
            convert_pdfa(p1, p2, opts.pdfa)
            final = p2
        if opts.meta.enabled or opts.pdfa != "Off":
            write_metadata(final, opts.meta)   # last step so nothing overwrites it
        return Path(final).read_bytes(), output_name(filename, opts.suffix, "pdf"), "application/pdf"


def preview(data: bytes, page_index: int, opts: Options, max_ppi: int = 110, seed: int = 7):
    """Quick low-res (original, scanned) preview of one page."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    n = len(doc)
    page_index = max(0, min(page_index, n - 1))
    ppi = min(opts.effects.ppi, max_ppi)
    img, _, _ = scan_page_image(doc[page_index], page_index, n, opts, ppi, random.Random(seed + page_index))
    pix = doc[page_index].get_pixmap(matrix=pymupdf.Matrix(ppi / 72, ppi / 72), alpha=False)
    orig = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return orig, img.convert("RGB"), n
