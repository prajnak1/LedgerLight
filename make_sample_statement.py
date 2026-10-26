"""Create a three-page image-only PDF for exercising Ledgerlight's local OCR."""
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "sample_statement_ocr.pdf"
W, H = 1275, 1650
pages = [
    ("JANUARY 2026", "01/01/2026 - 01/31/2026", "1,275.00", "3,398.40", [
        ("01/03/2026", "NORTHSTAR COFFEE", "8.45", ""),
        ("01/05/2026", "MONTHLY PAYROLL ACME CO", "", "2,450.00"),
        ("01/07/2026", "GREENWAY GROCERY", "86.32", ""),
        ("01/11/2026", "CITY TRANSIT PASS", "42.00", ""),
        ("01/14/2026", "STREAMBOX SUBSCRIPTION", "15.99", ""),
        ("01/18/2026", "PINE STREET PHARMACY", "23.74", ""),
        ("01/22/2026", "WATER AND POWER UTILITY", "118.60", ""),
        ("01/27/2026", "HARBOR BOOKS", "31.50", ""),
    ]),
    ("FEBRUARY 2026", "02/01/2026 - 02/28/2026", "3,398.40", "5,508.69", [
        ("02/02/2026", "NORTHSTAR COFFEE", "7.95", ""),
        ("02/05/2026", "MONTHLY PAYROLL ACME CO", "", "2,450.00"),
        ("02/08/2026", "GREENWAY GROCERY", "103.48", ""),
        ("02/10/2026", "CITY TRANSIT PASS", "42.00", ""),
        ("02/13/2026", "STREAMBOX SUBSCRIPTION", "15.99", ""),
        ("02/17/2026", "PINE STREET PHARMACY", "12.25", ""),
        ("02/20/2026", "WATER AND POWER UTILITY", "109.34", ""),
        ("02/25/2026", "WILLOW DINER", "48.70", ""),
    ]),
    ("MARCH 2026", "03/01/2026 - 03/31/2026", "5,508.69", "7,594.79", [
        ("03/02/2026", "NORTHSTAR COFFEE", "9.10", ""),
        ("03/05/2026", "MONTHLY PAYROLL ACME CO", "", "2,450.00"),
        ("03/08/2026", "GREENWAY GROCERY", "94.16", ""),
        ("03/11/2026", "CITY TRANSIT PASS", "42.00", ""),
        ("03/14/2026", "STREAMBOX SUBSCRIPTION", "15.99", ""),
        ("03/18/2026", "PINE STREET PHARMACY", "18.60", ""),
        ("03/22/2026", "WATER AND POWER UTILITY", "126.80", ""),
        ("03/28/2026", "GARDEN MARKET", "57.25", ""),
    ]),
]

def make_page(month, period, opening, ending, rows, page_number):
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    try:
        title = ImageFont.truetype(font_path, 42)
        regular = ImageFont.truetype(font_path, 25)
        small = ImageFont.truetype(font_path, 20)
        bold = ImageFont.truetype(font_path, 24)
    except OSError:
        title = regular = small = bold = ImageFont.load_default()
    d.text((95, 75), "NORTHSTAR COMMUNITY BANK", fill="#183c32", font=bold)
    d.text((95, 125), "EVERYDAY CHECKING  |  Account ending in 4821", fill="#344c43", font=regular)
    d.text((95, 220), "ACCOUNT STATEMENT", fill="#183c32", font=title)
    d.text((95, 285), f"{month}     Statement period: {period}", fill="#4f6259", font=regular)
    d.line((95, 340, W-95, 340), fill="#b9c8bf", width=3)
    d.text((100, 375), "Opening balance", fill="#344c43", font=regular)
    d.text((905, 375), f"${opening}", fill="#183c32", font=bold)
    d.text((100, 420), "Deposits and credits", fill="#344c43", font=regular)
    d.text((905, 420), "$2,450.00", fill="#183c32", font=bold)
    d.text((100, 465), "Closing balance", fill="#344c43", font=regular)
    d.text((905, 465), f"${ending}", fill="#183c32", font=bold)
    d.line((95, 525, W-95, 525), fill="#b9c8bf", width=3)
    d.text((95, 570), "TRANSACTION DETAIL", fill="#183c32", font=bold)
    d.rectangle((95, 625, W-95, 675), fill="#eaf2ed")
    d.text((110, 635), "DATE", fill="#38574a", font=small)
    d.text((340, 635), "DESCRIPTION", fill="#38574a", font=small)
    d.text((895, 635), "DEBIT", fill="#38574a", font=small)
    d.text((1080, 635), "CREDIT", fill="#38574a", font=small)
    y = 705
    for dt, desc, debit, credit in rows:
        d.text((110, y), dt, fill="#293d34", font=small)
        d.text((340, y), desc, fill="#293d34", font=small)
        if debit: d.text((895, y), f"${debit}", fill="#293d34", font=small)
        if credit: d.text((1080, y), f"${credit}", fill="#293d34", font=small)
        y += 83
        d.line((95, y-17, W-95, y-17), fill="#edf0ee", width=1)
    d.text((95, 1510), "Synthetic sample statement for software testing only. Not a real bank document.", fill="#75847c", font=small)
    d.text((W-185, 1570), f"Page {page_number} of 3", fill="#75847c", font=small)
    out = BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()

def pdf_from_jpegs(images):
    objects = [None, b"<< /Type /Catalog /Pages 2 0 R >>", None]
    page_refs = []
    for index, jpeg in enumerate(images):
        page_id = len(objects)
        image_id = page_id + 1
        content_id = page_id + 2
        content = b"q\n612 0 0 792 0 0 cm\n/Im0 Do\nQ\n"
        page_refs.append(f"{page_id} 0 R".encode())
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 {image_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(),
            f"<< /Type /XObject /Subtype /Image /Width {W} /Height {H} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\nstream\n".encode() + jpeg + b"\nendstream",
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream",
        ])
    objects[2] = b"<< /Type /Pages /Kids [" + b" ".join(page_refs) + f"] /Count {len(images)} >>".encode()
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for n, obj in enumerate(objects[1:], 1):
        offsets.append(len(pdf))
        pdf.extend(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return pdf

OUT.write_bytes(pdf_from_jpegs([make_page(*p, i) for i, p in enumerate(pages, 1)]))
print(f"Created {OUT}")
