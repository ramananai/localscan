# Look Scanned (offline) - Windows + Streamlit

Turns a PDF into a realistic scanned PDF, fully offline.

## Install (once)
1. Install **Python 3.10+** from python.org (tick "Add python.exe to PATH").
2. *(Only for PDF/A)* Install **Ghostscript 64-bit** from ghostscript.com/releases.
3. Double-click **run.bat**. First run installs packages, then opens the app in your browser.

Manual alternative:
    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    streamlit run app.py

## Features
- Scan effects: colorspace (colour / grayscale / B&W), border, rotation + random variance,
  brightness, contrast, blur, noise, yellowish tint, resolution (72-600 PPI)
- Paper size: Auto, A3, A4, A5, Letter, Legal
- Stamps and signatures: upload image (white background removal), typed handwriting signature,
  boxed text stamp, saved library (./signatures), position / size / rotation / opacity / page selection
- Watermark: text or image, single or tiled, opacity, angle
- PDF metadata: title, author, subject, keywords, producer, creator, creation + modification dates
  (Info dictionary and XMP kept in sync, written last)
- PDF/A-1b / PDF/A-2b (via Ghostscript)
- Output as PDF or ZIP of page images, filename suffix, batch of several PDFs, JPEG quality
- Preserve original text (invisible searchable layer; existing text only, no OCR, Latin characters only)

## Notes
- Signature fonts (Ink Free, Segoe Script, Lucida Handwriting, Brush Script...) come from Windows Fonts.
- PDF/A output is best-effort. For strict compliance, validate with veraPDF.
- Large PDFs at 400-600 PPI are slow and big; 150-200 PPI is typical of real scanners.
- Use responsibly: don't present a scanned copy as an original where authenticity matters.
=======
