"""
Look Scanned (offline) - Streamlit UI
Run:  streamlit run app.py
"""
import io
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from PIL import Image

import scanner as sc

st.set_page_config(page_title="Look Scanned (offline)", page_icon="🖨️", layout="wide")

SIG_DIR = Path(__file__).parent / "signatures"
SIG_DIR.mkdir(exist_ok=True)

ss = st.session_state
ss.setdefault("stamps", [])      # list of {"uid", "name", "image"}
ss.setdefault("uid", 0)
ss.setdefault("results", [])     # list of (bytes, name, mime)


def _remove_stamp(uid):
    ss.stamps = [s for s in ss.stamps if s["uid"] != uid]


def _add_stamp(name, image):
    ss.uid += 1
    ss.stamps.append({"uid": ss.uid, "name": name, "image": image})


st.title("🖨️ Look Scanned — offline")
st.caption("Turn a PDF into a realistic scanned PDF. Everything runs on this computer; nothing is uploaded.")

files = st.file_uploader("Select or drop your PDF file(s)", type=["pdf"], accept_multiple_files=True)

left, right = st.columns([1, 1.35], gap="large")

# =========================================================================== #
# SETTINGS
# =========================================================================== #
with left:
    t_scan, t_paper, t_stamp, t_wm, t_meta, t_out = st.tabs(
        ["Scan", "Paper", "Stamps", "Watermark", "Metadata", "Output"])

    # ---- Scan effects ---------------------------------------------------- #
    with t_scan:
        colorspace = st.selectbox("Colorspace", ["Grayscale", "Color", "Black & White"])
        border = st.checkbox("Border around the page", value=False)
        rotate = st.slider("Rotate (°)", -5.0, 5.0, 1.0, 0.1)
        rot_var = st.slider("Rotate variance (± °, random per page)", 0.0, 3.0, 0.5, 0.1)
        brightness = st.slider("Brightness (%)", 50, 150, 100)
        contrast = st.slider("Contrast (%)", 50, 200, 100)
        blur = st.slider("Blur (%)", 0, 100, 30)
        noise = st.slider("Noise (%)", 0, 100, 10)
        yellowish = st.slider("Yellowish (%)", 0, 100, 0)
        ppi = st.select_slider("Resolution (PPI)", [72, 100, 150, 200, 300, 400, 600], value=150)

    # ---- Paper ----------------------------------------------------------- #
    with t_paper:
        paper = st.selectbox("Paper size", list(sc.PAPER_SIZES.keys()),
                             help="A real scanner outputs a fixed paper size. Auto keeps each page's original size.")
        st.caption("Pages are scaled to fit and centred; orientation follows the page.")

    # ---- Stamps & signatures -------------------------------------------- #
    with t_stamp:
        st.markdown("**Add a stamp or signature**")
        mode = st.radio("Type", ["Upload image", "Typed signature", "Text stamp", "Saved library"], horizontal=True)
        new_img, new_name = None, ""

        if mode == "Upload image":
            up = st.file_uploader("Signature / stamp image (PNG, JPG)", type=["png", "jpg", "jpeg", "webp"], key="stamp_up")
            rm_bg = st.checkbox("Remove white background", value=True)
            thr = st.slider("Background threshold", 150, 250, 235, disabled=not rm_bg)
            if up:
                im = Image.open(up)
                new_img = sc.remove_white_background(im, thr) if rm_bg else im.convert("RGBA")
                new_name = Path(up.name).stem
        elif mode == "Typed signature":
            fonts = sc.available_signature_fonts() or ["(default font)"]
            sig_text = st.text_input("Name", "Your Name")
            sig_font = st.selectbox("Handwriting font", fonts)
            sig_col = st.color_picker("Ink colour", "#1a237e")
            if sig_text:
                new_img, new_name = sc.make_typed_signature(sig_text, sig_font, sig_col), f"sig-{sig_text}"
        elif mode == "Text stamp":
            stamp_text = st.text_input("Stamp text", "APPROVED")
            stamp_sub = st.text_input("Small line (optional)", "")
            stamp_col = st.color_picker("Stamp colour", "#c62828")
            stamp_border = st.checkbox("Boxed border", value=True)
            if stamp_text:
                new_img = sc.make_text_stamp(stamp_text, stamp_col, stamp_border, stamp_sub)
                new_name = f"stamp-{stamp_text}"
        else:
            saved = sorted(SIG_DIR.glob("*.png"))
            if saved:
                pick = st.selectbox("Saved items", [p.name for p in saved])
                new_img, new_name = Image.open(SIG_DIR / pick).convert("RGBA"), Path(pick).stem
            else:
                st.info(f"Library is empty. Saved items appear in:\n{SIG_DIR}")

        if new_img is not None:
            st.image(new_img, width=180)
            c1, c2 = st.columns(2)
            save_lib = c2.checkbox("Also save to library", value=False)
            if c1.button("➕ Add to document", type="primary"):
                _add_stamp(new_name or "stamp", new_img)
                if save_lib and mode != "Saved library":
                    new_img.save(SIG_DIR / f"{new_name or 'stamp'}.png")
                st.rerun()

        st.divider()
        st.markdown(f"**Placed on document ({len(ss.stamps)})**")
        for s in ss.stamps:
            u = s["uid"]
            with st.expander(f"{s['name']}", expanded=False):
                st.slider("Horizontal position (%)", 0, 100, 75, key=f"x{u}")
                st.slider("Vertical position (%)", 0, 100, 85, key=f"y{u}")
                st.slider("Size (% of page width)", 3, 80, 25, key=f"w{u}")
                st.slider("Rotation (°)", -180, 180, 0, key=f"r{u}")
                st.slider("Opacity (%)", 5, 100, 100, key=f"o{u}")
                st.text_input("Pages (all, first, last, or 1,3-5)", "all", key=f"p{u}")
                st.button("🗑 Remove", key=f"del{u}", on_click=_remove_stamp, args=(u,))

    # ---- Watermark ------------------------------------------------------- #
    with t_wm:
        wm_on = st.checkbox("Enable watermark", value=False)
        wm_kind = st.radio("Watermark type", ["Text", "Image"], horizontal=True, disabled=not wm_on)
        wm_text, wm_img = "CONFIDENTIAL", None
        if wm_kind == "Text":
            wm_text = st.text_input("Text", "CONFIDENTIAL", disabled=not wm_on)
            wm_color = st.color_picker("Colour", "#808080", disabled=not wm_on)
        else:
            wm_color = "#808080"
            wup = st.file_uploader("Watermark image", type=["png", "jpg", "jpeg", "webp"], key="wm_up", disabled=not wm_on)
            if wup:
                wm_img = Image.open(wup).convert("RGBA")
        wm_size = st.slider("Size (% of page width)", 2, 100, 8 if wm_kind == "Text" else 40, disabled=not wm_on)
        wm_opacity = st.slider("Opacity (%)", 3, 100, 20, disabled=not wm_on)
        wm_angle = st.slider("Angle (°)", -90, 90, 45, disabled=not wm_on)
        wm_tiled = st.checkbox("Tile across the page", value=False, disabled=not wm_on)
        wm_gap = st.slider("Tile spacing (%)", 0, 300, 100, disabled=not (wm_on and wm_tiled))

    # ---- Metadata -------------------------------------------------------- #
    with t_meta:
        meta_on = st.checkbox("Write custom PDF metadata", value=False,
                              help="Written as the very last step, after PDF/A conversion, so nothing overwrites it.")
        title_from_file = st.checkbox("Use the file name as Title", value=False, disabled=not meta_on)
        m_title = st.text_input("Title", disabled=not meta_on or title_from_file)
        m_author = st.text_input("Author", disabled=not meta_on)
        m_subject = st.text_input("Subject", disabled=not meta_on)
        m_keywords = st.text_input("Keywords (comma separated)", disabled=not meta_on)
        m_producer = st.text_input("Producer", disabled=not meta_on)
        m_creator = st.text_input("Creator", disabled=not meta_on)
        custom_dates = st.checkbox("Set creation / modification dates", value=False, disabled=not meta_on)
        d1, d2 = st.columns(2)
        now = datetime.now().replace(microsecond=0)
        cdate = d1.date_input("Creation date", now.date(), disabled=not (meta_on and custom_dates))
        ctime = d2.time_input("Creation time", now.time(), disabled=not (meta_on and custom_dates), key="ct")
        d3, d4 = st.columns(2)
        mdate = d3.date_input("Modification date", now.date(), disabled=not (meta_on and custom_dates))
        mtime = d4.time_input("Modification time", now.time(), disabled=not (meta_on and custom_dates), key="mt")

        st.divider()
        pdfa = st.selectbox("PDF/A (archiving)", ["Off", "PDF/A-1b", "PDF/A-2b"],
                            help="Many scanner apps save PDF/A. Needs Ghostscript installed.")
        if pdfa != "Off" and not sc.find_ghostscript():
            st.warning("Ghostscript was not found. Install it from ghostscript.com/releases (64-bit), "
                       "then restart this app.")

    # ---- Output ---------------------------------------------------------- #
    with t_out:
        out_fmt = st.radio("Output format", ["PDF", "Images (ZIP)"], horizontal=True)
        img_fmt = st.selectbox("Image format in ZIP", ["JPEG", "PNG"], disabled=out_fmt == "PDF")
        quality = st.slider("JPEG quality", 40, 100, 85, help="Lower = smaller file.")
        keep_text = st.checkbox("Preserve original text (searchable, invisible text layer)", value=False,
                                help="Keeps only text that already exists in the PDF. No OCR. Latin text only.")
        suffix = st.text_input("Filename suffix", "-scan")
        example = (Path(files[0].name).stem if files else "example") + suffix
        st.caption(f"Files will be saved as “{example}.{'pdf' if out_fmt == 'PDF' else 'zip'}”")


# --------------------------------------------------------------------------- #
# Build options from widgets
# --------------------------------------------------------------------------- #
def build_options(filename: str = "") -> sc.Options:
    stamps = []
    for s in ss.stamps:
        u = s["uid"]
        stamps.append(sc.Stamp(
            image=s["image"], x=ss.get(f"x{u}", 75), y=ss.get(f"y{u}", 85), width=ss.get(f"w{u}", 25),
            rotation=ss.get(f"r{u}", 0), opacity=ss.get(f"o{u}", 100), pages=ss.get(f"p{u}", "all")))
    cd = md = None
    if meta_on and custom_dates:
        cd, md = datetime.combine(cdate, ctime), datetime.combine(mdate, mtime)
    title = Path(filename).stem if (meta_on and title_from_file and filename) else m_title
    return sc.Options(
        effects=sc.Effects(colorspace, border, rotate, rot_var, brightness, contrast, blur, noise, yellowish, ppi),
        paper=paper,
        stamps=stamps,
        watermark=sc.Watermark(wm_on, wm_text, wm_img, wm_size, wm_opacity, wm_angle, wm_color, wm_tiled, wm_gap),
        meta=sc.Meta(meta_on, title, m_author, m_subject, m_keywords, m_producer, m_creator, cd, md),
        pdfa=pdfa, output_format=out_fmt, image_format=img_fmt, jpeg_quality=quality,
        keep_text=keep_text, suffix=suffix)


# =========================================================================== #
# PREVIEW + GENERATE
# =========================================================================== #
with right:
    if not files:
        st.info("Upload a PDF above to see the original and the scanned preview side by side.")
    else:
        names = [f.name for f in files]
        current = names[0] if len(names) == 1 else st.selectbox("Preview file", names)
        data = next(f for f in files if f.name == current).getvalue()

        import pymupdf
        n_pages = len(pymupdf.open(stream=data, filetype="pdf"))
        page_no = st.slider("Preview page", 1, n_pages, 1) if n_pages > 1 else 1

        try:
            with st.spinner("Rendering preview..."):
                orig, scan, _ = sc.preview(data, page_no - 1, build_options(current))
            c1, c2 = st.columns(2)
            c1.image(orig, caption="Original")
            c2.image(scan, caption="Scanned (preview, reduced resolution)")
        except Exception as e:  # keep the UI alive on bad input
            st.error(f"Preview failed: {e}")

        st.divider()
        if st.button("⚙️ Generate scanned file(s)", type="primary"):
            ss.results = []
            bar = st.progress(0.0, text="Starting...")
            try:
                for i, f in enumerate(files):
                    def cb(p, i=i, name=f.name):
                        bar.progress((i + p) / len(files), text=f"Scanning {name} ...")
                    ss.results.append(sc.process_pdf(f.getvalue(), f.name, build_options(f.name), cb))
                bar.progress(1.0, text="Done")
            except Exception as e:
                st.error(f"Generation failed: {e}")

        for idx, (b, name, mime) in enumerate(ss.results):
            st.download_button(f"⬇️ Download {name}  ({len(b) / 1024:.0f} KB)", b, file_name=name, mime=mime, key=f"dl{idx}")
        if len(ss.results) > 1:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for b, name, _ in ss.results:
                    z.writestr(name, b)
            st.download_button("⬇️ Download all (ZIP)", buf.getvalue(), file_name="scanned-files.zip", mime="application/zip")

st.caption("Tip: use the scanned copy responsibly. Don't present it as an original where authenticity matters "
           "(official, legal, or exam submissions).")
