"""Small helpers: time slots, coupons, uploads, UPI QR, invoice PDF."""
import os
import random
import uuid
from io import BytesIO
from datetime import date, datetime
from urllib.parse import urlencode, quote
from xml.sax.saxutils import escape

import qrcode
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

from models import Appointment, Coupon, Holiday, SalonSettings, BLOCKING_STATUSES

# ---------- small parsing helpers ----------
def hhmm_to_min(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_date(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def parse_ids(text):
    return [int(x) for x in (text or "").split(",") if x.strip().isdigit()]


def clean(text, limit=200):
    return (text or "").strip()[:limit]


# ---------- availability ----------
def get_slots(staff, day, duration, exclude_id=None):
    """Return start times (minutes after midnight) that are free for this staff member.
    A slot is free when the whole service (start -> start+duration) fits inside working
    hours and does not overlap any other appointment of that staff member."""
    site = SalonSettings.get()
    if day < date.today() or not staff.active:
        return []
    if day.weekday() in site.off_days or day.weekday() not in staff.days_list:
        return []
    if Holiday.query.filter_by(date=day).first():
        return []

    open_m = max(hhmm_to_min(site.open_time), hhmm_to_min(staff.start_time))
    close_m = min(hhmm_to_min(site.close_time), hhmm_to_min(staff.end_time))

    query = Appointment.query.filter(Appointment.staff_id == staff.id,
                                     Appointment.date == day,
                                     Appointment.status.in_(BLOCKING_STATUSES))
    if exclude_id:
        query = query.filter(Appointment.id != exclude_id)
    busy = [(a.start_min, a.start_min + a.duration) for a in query]

    now_min = None
    if day == date.today():
        now = datetime.now()
        now_min = now.hour * 60 + now.minute

    slots, t = [], open_m
    while t + duration <= close_m:
        overlaps = any(t < end and t + duration > begin for begin, end in busy)
        if not overlaps and (now_min is None or t > now_min):
            slots.append(t)
        t += 30
    return slots


# ---------- pricing ----------
def check_coupon(code, subtotal):
    """Return (coupon, error_message)."""
    coupon = Coupon.query.filter_by(code=code.strip().upper()).first()
    if not coupon or not coupon.active:
        return None, "That coupon code is not valid."
    if coupon.expiry and coupon.expiry < date.today():
        return None, "That coupon has expired."
    if subtotal < (coupon.min_amount or 0):
        return None, f"This coupon needs a minimum booking of Rs. {coupon.min_amount:,.0f}."
    return coupon, None


def calc_totals(services, coupon, tax_percent):
    subtotal = round(sum(s.final_price for s in services), 2)
    discount = coupon.discount_for(subtotal) if coupon else 0
    tax = round((subtotal - discount) * (tax_percent or 0) / 100, 2)
    total = round(subtotal - discount + tax, 2)
    return {"subtotal": subtotal, "discount": discount, "tax": tax, "total": total,
            "duration": sum(s.duration for s in services)}


# ---------- uploads ----------
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "gif"}


def save_image(file, folder, prefix=""):
    """Validate and save an uploaded image under a random name. Returns filename or None."""
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXT:
        raise ValueError("Only PNG, JPG, WEBP or GIF images are allowed.")
    try:
        Image.open(file.stream).verify()      # is it really an image?
    except Exception:
        raise ValueError("That file is not a valid image.")
    file.stream.seek(0)
    os.makedirs(folder, exist_ok=True)
    name = f"{prefix}{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(folder, name))
    return name


def delete_file(folder, name):
    if name:
        try:
            os.remove(os.path.join(folder, os.path.basename(name)))
        except OSError:
            pass


def make_art(path, c1, c2, seed=0):
    """Soft gradient picture used as a placeholder until real photos are uploaded."""
    w, h = 900, 600
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    rnd = random.Random(seed)
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for _ in range(5):
        r, x, y = rnd.randint(80, 220), rnd.randint(0, w), rnd.randint(0, h)
        od.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(25, 65)))
    over = over.filter(ImageFilter.GaussianBlur(35))
    Image.alpha_composite(img.convert("RGBA"), over).convert("RGB").save(path, quality=85)


# ---------- UPI QR ----------
def upi_link(upi_id, name, amount, ref):
    params = {"pa": upi_id, "pn": name, "am": f"{amount:.2f}", "cu": "INR", "tn": ref}
    return "upi://pay?" + urlencode(params, quote_via=quote)


def upi_qr_png(upi_id, name, amount, ref):
    img = qrcode.make(upi_link(upi_id, name, amount, ref))
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ---------- invoice PDF ----------
def build_invoice(appt, site):
    def rs(v):
        return f"Rs. {v:,.2f}"

    ink = colors.HexColor("#16302F")
    muted = colors.HexColor("#5B6B69")
    gold = colors.HexColor("#C8A46B")
    base = getSampleStyleSheet()["Normal"]
    title = ParagraphStyle("t", parent=base, fontName="Helvetica-Bold", fontSize=20, textColor=ink, leading=24)
    small = ParagraphStyle("s", parent=base, fontSize=9, textColor=muted, leading=13)
    right = ParagraphStyle("r", parent=small, alignment=2)
    right_big = ParagraphStyle("rb", parent=right, fontName="Helvetica-Bold", fontSize=16, textColor=ink, leading=20)
    body = ParagraphStyle("b", parent=base, fontSize=10, leading=14)
    bold = ParagraphStyle("bb", parent=body, fontName="Helvetica-Bold")

    def P(text, style=body):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"Invoice {appt.booking_no}")
    story = []

    head = Table([
        [P(site.salon_name, title), P("INVOICE", right_big)],
        [P(f"{site.address}\n{site.phone}  |  {site.email}", small),
         P(f"Booking no: {appt.booking_no}\nIssued: {appt.created_at:%d %b %Y}", right)],
    ], colWidths=[105 * mm, 69 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LINEBELOW", (0, 1), (-1, 1), 1.2, gold),
                              ("BOTTOMPADDING", (0, 1), (-1, 1), 8)]))
    story += [head, Spacer(1, 8 * mm)]

    info = Table([
        [P("Billed to", small), P("Appointment", small)],
        [P(f"{appt.customer.name}\n{appt.customer.email}\n{appt.customer.phone or ''}", body),
         P(f"{appt.date:%A, %d %B %Y}\n{appt.time_label} - {appt.end_label}\nWith {appt.staff.name}\nStatus: {appt.status}", body)],
    ], colWidths=[87 * mm, 87 * mm])
    info.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    story += [info, Spacer(1, 8 * mm)]

    rows = [[P("Service", bold), P("Duration", bold), P("Price", bold)]]
    for s in appt.services:
        rows.append([P(s.name), P(f"{s.duration} min"), P(rs(s.final_price))])
    items = Table(rows, colWidths=[104 * mm, 30 * mm, 40 * mm])
    items.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1EDE4")),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
                               ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [items, Spacer(1, 5 * mm)]

    totals = [["Subtotal", rs(appt.subtotal)]]
    if appt.discount:
        totals.append([f"Discount ({appt.coupon_code})" if appt.coupon_code else "Discount", "- " + rs(appt.discount)])
    if appt.tax:
        totals.append(["Tax", rs(appt.tax)])
    totals += [["Total", rs(appt.total)], ["Paid (verified)", rs(appt.paid_amount)],
               ["Remaining", rs(appt.remaining)], ["Payment status", appt.payment_status]]
    tt = Table([[P(a, body), P(b, ParagraphStyle("x", parent=body, alignment=2))] for a, b in totals],
               colWidths=[134 * mm, 40 * mm], hAlign="RIGHT")
    last_total = 1 + (1 if appt.discount else 0) + (1 if appt.tax else 0)
    tt.setStyle(TableStyle([("LINEABOVE", (0, last_total), (-1, last_total), 0.8, ink),
                            ("FONTNAME", (0, last_total), (-1, last_total), "Helvetica-Bold"),
                            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [tt, Spacer(1, 12 * mm),
              P("Thank you for choosing " + site.salon_name + ". This is a computer-generated invoice.", small)]
    doc.build(story)
    buf.seek(0)
    return buf
