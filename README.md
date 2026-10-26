# Ledgerlight

A local-first statement review app. Upload PDF or image statements, extract transaction candidates with local OCR, review and correct low-confidence rows, compare confirmed cash flow by month, and view a basic next-month baseline. Each transaction links to its source statement and PDF page.

## Run it

Requires Python 3.10+ and the Tesseract OCR executable.

1. Install Tesseract (the OCR engine) using your platform package manager:
   - macOS: `brew install tesseract`
   - Ubuntu/Debian: `sudo apt install tesseract-ocr`
   - Windows: install the Tesseract OCR build and add its install directory to `PATH`.
2. From this folder, create an environment and install app dependencies:

   ```sh
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Start the app:

   ```sh
   uvicorn app:app --reload
   ```

4. Open http://127.0.0.1:8000.

Statements and the SQLite ledger are stored under `data/` in this project folder. Keep that directory private and back it up only where you intend. The OCR and transaction parsing run on the machine hosting the app; the UI uses Google Fonts for typography when internet access is available, with local system fallbacks.

## OCR implementation notes

`app.py` first extracts the embedded text from every PDF page with PyMuPDF. If a page is image-only or has very little text, it renders that page at 2× resolution and sends the pixels to Tesseract through `pytesseract`. Uploaded PNG/JPEG/TIFF files go directly to Tesseract. The parser scans each OCR text line for a date and amount, stores the page number and a heuristic confidence score, then leaves entries unconfirmed for a person to review. Nothing is sent to a hosted OCR service.

To improve OCR quality, install the correct Tesseract language packs, deskew/clean low-quality scans, and render at 300 DPI or higher for small print. For real financial use, replace the demo line parser with institution-specific parsers or a local document-layout model. Statement layouts vary: an amount column may omit minus signs, debit/credit may be shown in separate columns, and balances can be mistaken for transactions. Treat extracted rows as suggestions and verify them against the linked page. Confidence here is a transparent heuristic, not a calibrated probability.

## What the prototype includes

- PDF and image upload, page-by-page local OCR/text extraction, and transaction candidates.
- Review queue, confirm action, editable date/description/signed amount, and dynamic summaries after edits.
- Confirmed monthly income, expenses, and net comparison; a feature-engineered forecast using repeated merchant descriptions, rule-based spending categories, and recency-weighted variable cash flows from up to three confirmed months.
- Source page links for each transaction and original statement access.

The parser expects common numeric dates and dollar amounts. The forecast uses transparent heuristics: a description seen in at least two months is treated as recurring and projected at its median monthly amount; other income and category-level expenses use a recency-weighted average (50% newest month, 30% prior month, 20% oldest available month). It does not model seasonality or include a confidence interval, and merchant matching is based on normalized descriptions. This is a starter project, not an accounting reconciliation system.
