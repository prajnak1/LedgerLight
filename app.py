from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import statistics
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import fitz
import pytesseract
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent
DATA = ROOT / "data"
UPLOADS = DATA / "statements"
DB = DATA / "ledger.db"
UPLOADS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Ledgerlight", description="Local-first statement review and cash-flow forecasting")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS statements (
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL, stored_name TEXT NOT NULL,
          uploaded_at TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
          txn_date TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL,
          kind TEXT NOT NULL, page INTEGER NOT NULL DEFAULT 1, confidence REAL NOT NULL DEFAULT 0.5,
          confirmed INTEGER NOT NULL DEFAULT 0
        );
        """)


init_db()


def extract_pdf(path: Path) -> tuple[list[tuple[int, str]], int]:
    doc = fitz.open(path)
    pages: list[tuple[int, str]] = []
    for idx, page in enumerate(doc, 1):
        text = page.get_text("text").strip()
        if len(text) < 30:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                text = ocr_layout_text(image)
            except pytesseract.TesseractNotFoundError as exc:
                raise HTTPException(500, "Local Tesseract OCR is not installed. See README.md for setup.") from exc
        pages.append((idx, text))
    return pages, len(doc)


def extract_image(path: Path) -> tuple[list[tuple[int, str]], int]:
    from PIL import Image
    try:
        text = ocr_layout_text(Image.open(path))
    except pytesseract.TesseractNotFoundError as exc:
        raise HTTPException(500, "Local Tesseract OCR is not installed. See README.md for setup.") from exc
    return [(1, text)], 1


def ocr_layout_text(image) -> str:
    """Rebuild OCR rows by vertical position, retaining debit/credit column cues."""
    from pytesseract import Output

    data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 6")
    words = []
    for i, raw in enumerate(data["text"]):
        word = raw.strip()
        if not word:
            continue
        try:
            if float(data["conf"][i]) < 20:
                continue
        except (TypeError, ValueError):
            pass
        y = int(data["top"][i]) + int(data["height"][i]) / 2
        words.append((y, int(data["left"][i]), word))
    words.sort()
    rows: list[list[tuple[float, int, str]]] = []
    for token in words:
        y = token[0]
        if not rows or abs(y - sum(w[0] for w in rows[-1]) / len(rows[-1])) > 18:
            rows.append([])
        rows[-1].append(token)
    rendered = []
    for row in rows:
        # Explicit labels make unsigned bank-statement amount columns meaningful.
        row_text = []
        for y, x, word in sorted(row, key=lambda item: item[1]):
            if re.fullmatch(r"\$?\(?-?\d[\d,]*\.\d{2}\)?[,;]?", word):
                if x > image.width * .76:
                    row_text.append("CREDIT")
                elif x > image.width * .61:
                    row_text.append("DEBIT")
            row_text.append(word)
        rendered.append(" ".join(row_text))
    return "\n".join(rendered)


DATE_RE = re.compile(r"(?P<date>\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})\b)")
MONEY_RE = re.compile(r"(?:\$\s*)?\(?-?\d{1,3}(?:,\d{3})*\.\d{2}\)?")


def normalize_date(raw: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw, fmt)
            if fmt in ("%m/%d", "%m-%d"):
                d = d.replace(year=date.today().year)
            return d.date().isoformat()
        except ValueError:
            continue
    return None


def parse_transactions(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    found = []
    for page_num, text in pages:
        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            dm = DATE_RE.search(line)
            amounts = MONEY_RE.findall(line)
            if not dm or not amounts:
                continue
            dt = normalize_date(dm.group("date"))
            if not dt:
                continue
            raw = amounts[-1].replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
            try:
                amount = abs(float(raw))
            except ValueError:
                continue
            if amount == 0 or amount > 1_000_000:
                continue
            desc = line[:dm.start()] + line[dm.end():]
            desc = desc.replace(amounts[-1], " ")
            desc = re.sub(r"\b(?:\$|USD|debit|credit)\b", "", desc, flags=re.I)
            desc = re.sub(r"\s+", " ", desc).strip(" -|:,") or "Unlabeled transaction"
            # Bank-statement convention: parentheses or explicit minus indicate outflow.
            token = amounts[-1]
            is_outflow = "(" in token or "-" in token or bool(re.search(r"\b(debit|withdrawal|purchase)\b", line, re.I))
            signed = -amount if is_outflow else amount
            confidence = 0.88 if desc != "Unlabeled transaction" and len(line) >= 18 else 0.62
            found.append({"date": dt, "description": desc[:120], "amount": signed,
                          "kind": "expense" if signed < 0 else "income", "page": page_num,
                          "confidence": confidence})
    # Remove duplicate captures from wrapped/overlapping OCR lines.
    unique = {}
    for row in found:
        key = (row["date"], row["description"].casefold(), row["amount"], row["page"])
        unique[key] = row
    return list(unique.values())


def expense_category(description: str) -> str:
    text = description.casefold()
    rules = {
        "housing": ("rent", "mortgage", "property management"),
        "utilities": ("utility", "electric", "power", "water", "internet", "gas bill"),
        "groceries": ("grocery", "grocer", "market", "food store"),
        "transport": ("transit", "parking", "gas station", "uber", "lyft", "train", "airline"),
        "subscriptions": ("subscription", "stream", "membership", "netflix", "spotify"),
        "health": ("pharmacy", "medical", "clinic", "health", "dental"),
        "dining": ("cafe", "coffee", "restaurant", "diner", "pizza", "takeout"),
        "shopping": ("books", "store", "shop", "clothing", "hardware"),
        "fees": ("fee", "interest charge", "service charge"),
    }
    for category, words in rules.items():
        if any(word in text for word in words):
            return category
    return "other"


def weighted_recent_average(values: list[float]) -> float:
    """Recency-weight values ordered oldest to newest, using up to 3 months."""
    recent = values[-3:]
    weights = [0.2, 0.3, 0.5][-len(recent):]
    scale = sum(weights)
    return sum(v * w for v, w in zip(recent, weights)) / scale if recent else 0.0


def engineered_forecast(confirmed: list[dict[str, Any]], month_keys: list[str]) -> dict[str, Any]:
    """Forecast net flow from recurring-source, category, and recency features."""
    history = month_keys[-3:]
    if not history:
        return {"net": 0.0, "income": 0.0, "expenses": 0.0,
                "recurring_income": 0.0, "variable_income": 0.0,
                "recurring_expenses": 0.0, "variable_expenses": 0.0,
                "categories": [], "history_months": [], "recurring_sources": 0}

    rows = []
    source_counts: dict[tuple[str, str], set[str]] = {}
    for txn in confirmed:
        month = txn["txn_date"][:7]
        if month not in history:
            continue
        amount = float(txn["amount"])
        desc = re.sub(r"[^a-z0-9]+", " ", txn["description"].casefold()).strip() or "unknown source"
        flow = "income" if amount > 0 else "expense"
        source = (flow, desc)
        source_counts.setdefault(source, set()).add(month)
        rows.append((txn, month, amount, source))

    recurring = {source for source, seen in source_counts.items() if len(seen) >= 2}
    recurring_income_by_source: dict[tuple[str, str], list[float]] = {}
    recurring_expense_by_source: dict[tuple[str, str], list[float]] = {}
    variable_income_by_month = {month: 0.0 for month in history}
    variable_expenses_by_category = {category: {month: 0.0 for month in history} for category in (
        "housing", "utilities", "groceries", "transport", "subscriptions", "health", "dining", "shopping", "fees", "other")}

    for txn, month, amount, source in rows:
        if source in recurring:
            target = recurring_income_by_source if amount > 0 else recurring_expense_by_source
            target.setdefault(source, [0.0 for _ in history])
            target[source][history.index(month)] += abs(amount)
        elif amount > 0:
            variable_income_by_month[month] += amount
        else:
            category = expense_category(txn["description"])
            variable_expenses_by_category[category][month] += abs(amount)

    recurring_income = sum(statistics.median(month_values) for month_values in recurring_income_by_source.values())
    recurring_expenses_by_category: dict[str, float] = {}
    for (_, source_name), month_values in recurring_expense_by_source.items():
        category = expense_category(source_name)
        recurring_expenses_by_category[category] = recurring_expenses_by_category.get(category, 0.0) + statistics.median(month_values)
    recurring_expenses = sum(recurring_expenses_by_category.values())
    variable_income = weighted_recent_average([variable_income_by_month[m] for m in history])
    category_features = []
    variable_expenses = 0.0
    for category, totals in variable_expenses_by_category.items():
        estimate = weighted_recent_average([totals[m] for m in history])
        category_total = recurring_expenses_by_category.get(category, 0.0) + estimate
        if category_total > 0:
            category_features.append({"category": category, "monthly_estimate": round(category_total, 2)})
        if estimate > 0:
            variable_expenses += estimate
    forecast_income = recurring_income + variable_income
    forecast_expenses = recurring_expenses + variable_expenses
    return {
        "net": round(forecast_income - forecast_expenses, 2),
        "income": round(forecast_income, 2),
        "expenses": round(forecast_expenses, 2),
        "recurring_income": round(recurring_income, 2),
        "variable_income": round(variable_income, 2),
        "recurring_expenses": round(recurring_expenses, 2),
        "variable_expenses": round(variable_expenses, 2),
        "categories": category_features,
        "history_months": history,
        "recurring_sources": len(recurring),
    }


@app.get("/api/dashboard")
def dashboard():
    with db() as con:
        tx = [dict(r) for r in con.execute("""SELECT t.*, s.filename FROM transactions t
            JOIN statements s ON s.id=t.statement_id ORDER BY txn_date DESC, t.id DESC""")]
        statements = [dict(r) for r in con.execute("""SELECT s.*,
            (SELECT COUNT(*) FROM transactions t WHERE t.statement_id=s.id) AS transaction_count
            FROM statements s ORDER BY s.uploaded_at DESC""")]
    confirmed = [t for t in tx if t["confirmed"]]
    months: dict[str, dict[str, float]] = {}
    for t in confirmed:
        month = t["txn_date"][:7]
        acc = months.setdefault(month, {"income": 0, "expenses": 0, "net": 0})
        if t["amount"] > 0: acc["income"] += t["amount"]
        else: acc["expenses"] += abs(t["amount"])
        acc["net"] += t["amount"]
    month_keys = sorted(months)
    recent = month_keys[-2:]
    comparison = [{"month": m, **months[m]} for m in recent]
    forecast_features = engineered_forecast(confirmed, month_keys)
    income = sum(t["amount"] for t in confirmed if t["amount"] > 0)
    expenses = sum(abs(t["amount"]) for t in confirmed if t["amount"] < 0)
    return {"transactions": tx, "statements": statements, "summary": {
        "income": round(income, 2), "expenses": round(expenses, 2), "net": round(income-expenses, 2),
        "needs_review": sum(1 for t in tx if not t["confirmed"] or t["confidence"] < .75),
        "forecast": forecast_features["net"], "forecast_features": forecast_features,
        "forecast_months": month_keys[-3:], "comparison": comparison,
        "confirmed_count": len(confirmed), "month_count": len(month_keys)}}


@app.post("/api/statements")
async def upload_statement(file: UploadFile = File(...)):
    suffix = Path(file.filename or "statement.pdf").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        raise HTTPException(400, "Upload a PDF or image statement (PNG/JPG/TIFF).")
    content = await file.read()
    if not content or len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "File is empty or exceeds the 25 MB limit.")
    stored = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    path = UPLOADS / stored
    path.write_bytes(content)
    pages, page_count = extract_pdf(path) if suffix == ".pdf" else extract_image(path)
    extracted = parse_transactions(pages)
    with db() as con:
        cur = con.execute("INSERT INTO statements(filename, stored_name, uploaded_at, pages) VALUES(?,?,?,?)",
                          (Path(file.filename or "statement").name, stored, datetime.now().isoformat(timespec="seconds"), page_count))
        statement_id = cur.lastrowid
        for t in extracted:
            con.execute("""INSERT INTO transactions(statement_id,txn_date,description,amount,kind,page,confidence,confirmed)
                VALUES(?,?,?,?,?,?,?,0)""", (statement_id,t["date"],t["description"],t["amount"],t["kind"],t["page"],t["confidence"]))
    return {"statement_id": statement_id, "filename": Path(file.filename or "statement").name,
            "pages": page_count, "extracted": len(extracted), "needs_review": len(extracted)}


class TransactionUpdate(BaseModel):
    date: str
    description: str
    amount: float
    confirmed: bool


@app.patch("/api/transactions/{transaction_id}")
def update_transaction(transaction_id: int, update: TransactionUpdate):
    try:
        date.fromisoformat(update.date)
    except ValueError as exc:
        raise HTTPException(400, "Date must be YYYY-MM-DD.") from exc
    if not update.description.strip() or abs(update.amount) > 10_000_000:
        raise HTTPException(400, "Enter a description and a reasonable amount.")
    with db() as con:
        cur = con.execute("UPDATE transactions SET txn_date=?, description=?, amount=?, kind=?, confirmed=? WHERE id=?",
            (update.date, update.description.strip(), update.amount, "expense" if update.amount < 0 else "income", int(update.confirmed), transaction_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Transaction not found.")
    return {"ok": True}


@app.get("/api/statements/{statement_id}/file")
def statement_file(statement_id: int):
    with db() as con:
        row = con.execute("SELECT filename,stored_name FROM statements WHERE id=?", (statement_id,)).fetchone()
    if not row: raise HTTPException(404, "Statement not found.")
    return FileResponse(UPLOADS / row["stored_name"], filename=row["filename"])


@app.post("/api/statements/{statement_id}/reprocess")
def reprocess_statement(statement_id: int):
    with db() as con:
        statement = con.execute("SELECT * FROM statements WHERE id=?", (statement_id,)).fetchone()
    if not statement:
        raise HTTPException(404, "Statement not found.")
    path = UPLOADS / statement["stored_name"]
    if not path.is_file():
        raise HTTPException(404, "The uploaded statement file is missing from local storage.")
    suffix = path.suffix.lower()
    pages, page_count = extract_pdf(path) if suffix == ".pdf" else extract_image(path)
    extracted = parse_transactions(pages)
    with db() as con:
        con.execute("DELETE FROM transactions WHERE statement_id=?", (statement_id,))
        for t in extracted:
            con.execute("""INSERT INTO transactions(statement_id,txn_date,description,amount,kind,page,confidence,confirmed)
                VALUES(?,?,?,?,?,?,?,0)""", (statement_id,t["date"],t["description"],t["amount"],t["kind"],t["page"],t["confidence"]))
        con.execute("UPDATE statements SET pages=? WHERE id=?", (page_count, statement_id))
    return {"statement_id": statement_id, "extracted": len(extracted), "pages": page_count}


@app.delete("/api/transactions/{transaction_id}")
def delete_transaction(transaction_id: int):
    with db() as con:
        con.execute("DELETE FROM transactions WHERE id=?", (transaction_id,))
    return {"ok": True}


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
