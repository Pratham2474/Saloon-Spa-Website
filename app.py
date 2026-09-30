import csv
import io
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, flash, jsonify,
                   abort, send_file, Response)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect, CSRFError
from sqlalchemy import func, or_

from config import Config
from models import (db, User, ServiceCategory, Service, Staff, Appointment, Payment, Coupon, Review,
                    Gallery, Holiday, SalonSettings, appointment_services, STATUSES, ACTIVE_STATUSES,
                    DAY_NAMES)
from utils.helpers import (get_slots, check_coupon, calc_totals, save_image, delete_file, upi_link,
                           upi_qr_png, build_invoice, to_int, to_float, parse_date, parse_ids, clean)
from utils.email_utils import send_email_async
from utils.seed import seed

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "info"

DAY_KEYS = [str(i) for i in range(7)]
BOOK_LOCK = threading.Lock()      # makes "check slot + save booking" one step (no double booking)
FAILED_LOGINS = {}                # simple brute-force protection
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9 ]{10,15}$")
UPI_RE = re.compile(r"^[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9]{1,}$")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def safe_next(url):
    """Only allow redirects to pages on this site."""
    if url and url.startswith("/") and not url.startswith("//"):
        return url
    return None


def mine_or_admin(user_id):
    if user_id != current_user.id and not current_user.is_admin:
        abort(404)


@app.context_processor
def inject_globals():
    return {"site": SalonSettings.get(), "today": date.today(), "DAY_NAMES": DAY_NAMES,
            "STATUSES": STATUSES}


@app.template_filter("money")
def money(value):
    return f"{(value or 0):,.2f}"


@app.template_filter("fdate")
def fdate(value):
    return value.strftime("%a, %d %b %Y") if value else ""


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def error_page(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(403)
def e403(e):
    return error_page(403, "Not allowed", "You don't have permission to open this page.")


@app.errorhandler(404)
def e404(e):
    return error_page(404, "Page not found", "We couldn't find what you were looking for.")


@app.errorhandler(413)
def e413(e):
    return error_page(413, "File too large", "Uploads can be up to 5 MB.")


@app.errorhandler(CSRFError)
def ecsrf(e):
    return error_page(400, "Session expired", "Please go back, refresh the page and try again.")


# ======================= PUBLIC PAGES =======================
@app.route("/")
def index():
    categories = ServiceCategory.query.order_by(ServiceCategory.id).all()
    featured = [c.active_services[0] for c in categories if c.active_services][:6]
    offers = Service.query.filter(Service.active.is_(True), Service.discount_price.isnot(None)) \
        .order_by(Service.id).limit(4).all()
    offers = [s for s in offers if s.has_discount]
    reviews = Review.query.filter_by(status="Approved").order_by(Review.id.desc()).limit(6).all()
    avg = db.session.query(func.avg(Review.rating)).filter(Review.status == "Approved").scalar()
    gallery = Gallery.query.filter_by(approved=True).order_by(Gallery.id.desc()).limit(6).all()
    team = Staff.query.filter_by(active=True).all()
    return render_template("index.html", categories=categories, featured=featured, offers=offers,
                           reviews=reviews, avg=avg, gallery=gallery, team=team,
                           all_services=Service.query.filter_by(active=True).order_by(Service.name).all())


@app.route("/services")
def services():
    q = clean(request.args.get("q"), 60)
    cat_id = to_int(request.args.get("category"))
    query = Service.query.filter_by(active=True)
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if q:
        query = query.filter(or_(Service.name.ilike(f"%{q}%"), Service.description.ilike(f"%{q}%")))
    return render_template("services.html", items=query.order_by(Service.category_id, Service.id).all(),
                           categories=ServiceCategory.query.order_by(ServiceCategory.id).all(),
                           q=q, cat_id=cat_id)


@app.route("/services/<int:service_id>")
def service_detail(service_id):
    service = db.session.get(Service, service_id)
    if not service or not service.active:
        abort(404)
    related = [s for s in service.category.active_services if s.id != service.id][:3]
    return render_template("service_detail.html", s=service, related=related)


@app.route("/gallery")
def gallery():
    return render_template("gallery.html", items=Gallery.query.filter_by(approved=True).order_by(Gallery.id.desc()).all())


# ======================= AUTH =======================
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name, email = clean(request.form.get("name"), 100), clean(request.form.get("email"), 120).lower()
        phone, pw = clean(request.form.get("phone"), 20), request.form.get("password", "")
        errors = []
        if len(name) < 2:
            errors.append("Please enter your name.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        if not PHONE_RE.match(phone):
            errors.append("Please enter a valid phone number (10-15 digits).")
        if len(pw) < 8:
            errors.append("Password must be at least 8 characters.")
        if pw != request.form.get("confirm", ""):
            errors.append("The two passwords do not match.")
        if not errors and User.query.filter_by(email=email).first():
            errors.append("An account with this email already exists. Try logging in.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=request.form)
        user = User(name=name, email=email, phone=phone)
        user.set_password(pw)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome! Your account is ready.", "success")
        return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = clean(request.form.get("email"), 120).lower()
        key = (email, request.remote_addr)
        rec = FAILED_LOGINS.get(key)
        if rec and rec["until"] > datetime.now():
            flash("Too many failed attempts. Please wait 10 minutes and try again.", "danger")
            return render_template("login.html")
        user = User.query.filter_by(email=email).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            FAILED_LOGINS.pop(key, None)
            login_user(user)
            if user.is_admin:
                return redirect(safe_next(request.args.get("next")) or url_for("admin_dashboard"))
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
        rec = FAILED_LOGINS.setdefault(key, {"n": 0, "until": datetime.min})
        rec["n"] += 1
        if rec["n"] >= 5:
            rec.update(n=0, until=datetime.now() + timedelta(minutes=10))
        flash("Email or password is incorrect." if not (user and not user.active)
              else "This account has been deactivated. Please contact the salon.", "danger")
    elif not User.query.filter_by(is_admin=True).first():
        return redirect(url_for("setup"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """Create the first admin. Only works while no admin exists and needs the setup key."""
    if User.query.filter_by(is_admin=True).first():
        abort(404)
    if request.method == "POST":
        name, email = clean(request.form.get("name"), 100), clean(request.form.get("email"), 120).lower()
        pw = request.form.get("password", "")
        if not secrets.compare_digest(request.form.get("setup_key", "").strip().encode(), Config.SETUP_KEY.encode()):
            flash("Setup key is wrong. Check the console where you started the app (or SETUP_KEY in .env).", "danger")
        elif len(name) < 2 or not EMAIL_RE.match(email):
            flash("Enter a name and a valid email.", "danger")
        elif len(pw) < 10:
            flash("Admin password must be at least 10 characters.", "danger")
        elif pw != request.form.get("confirm", ""):
            flash("The two passwords do not match.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("That email is already registered as a customer. Use a different one.", "danger")
        else:
            admin = User(name=name, email=email, is_admin=True)
            admin.set_password(pw)
            db.session.add(admin)
            db.session.commit()
            login_user(admin)
            flash("Admin account created. Add your UPI ID in Settings to accept UPI payments.", "success")
            return redirect(url_for("admin_settings"))
    return render_template("setup.html")


# ======================= CUSTOMER DASHBOARD =======================
@app.route("/dashboard")
@login_required
def dashboard():
    appts = Appointment.query.filter_by(user_id=current_user.id).order_by(Appointment.date, Appointment.start_min).all()
    now = datetime.now()
    upcoming = [a for a in appts if a.status in ACTIVE_STATUSES and a.starts_at >= now]
    past = [a for a in reversed(appts) if a not in upcoming]
    payments = Payment.query.join(Appointment).filter(Appointment.user_id == current_user.id) \
        .order_by(Payment.id.desc()).all()
    return render_template("dashboard.html", upcoming=upcoming, past=past, payments=payments)


@app.route("/profile", methods=["POST"])
@login_required
def profile():
    name, phone = clean(request.form.get("name"), 100), clean(request.form.get("phone"), 20)
    if len(name) < 2 or not PHONE_RE.match(phone):
        flash("Please enter a valid name and phone number.", "danger")
        return redirect(url_for("dashboard") + "#profile")
    new_pw = request.form.get("new_password", "")
    if new_pw:
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("dashboard") + "#profile")
        if len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
            return redirect(url_for("dashboard") + "#profile")
        current_user.set_password(new_pw)
    current_user.name, current_user.phone = name, phone
    db.session.commit()
    flash("Profile updated.", "success")
    return redirect(url_for("dashboard") + "#profile")


# ======================= BOOKING =======================
@app.route("/api/staff")
def api_staff():
    ids = parse_ids(request.args.get("services"))
    query = Staff.query.filter_by(active=True)
    for sid in ids:
        query = query.filter(Staff.services.any(Service.id == sid))
    if not ids:
        return jsonify([])
    return jsonify([{"id": s.id, "name": s.name, "designation": s.designation,
                     "photo": url_for("static", filename="uploads/" + s.photo) if s.photo else None}
                    for s in query.all()])


@app.route("/api/slots")
def api_slots():
    day = parse_date(request.args.get("date"))
    if not day:
        return jsonify([])
    exclude = None
    appt_id = to_int(request.args.get("appointment"))
    if appt_id:                                       # rescheduling an existing booking
        if not current_user.is_authenticated:
            abort(403)
        appt = db.session.get(Appointment, appt_id)
        if not appt:
            abort(404)
        mine_or_admin(appt.user_id)
        staff, duration, exclude = appt.staff, appt.duration, appt.id
    else:
        ids = parse_ids(request.args.get("services"))
        services = Service.query.filter(Service.id.in_(ids), Service.active.is_(True)).all() if ids else []
        staff = db.session.get(Staff, to_int(request.args.get("staff")))
        if not services or not staff or not all(s in staff.services for s in services):
            return jsonify([])
        duration = sum(s.duration for s in services)
    return jsonify([{"value": m, "label": Appointment.label(m)}
                    for m in get_slots(staff, day, duration, exclude)])


@app.route("/api/quote")
def api_quote():
    ids = parse_ids(request.args.get("services"))
    services = Service.query.filter(Service.id.in_(ids), Service.active.is_(True)).all() if ids else []
    site = SalonSettings.get()
    code = clean(request.args.get("coupon"), 30)
    coupon, msg = None, ""
    if code and services:
        coupon, err = check_coupon(code, round(sum(s.final_price for s in services), 2))
        msg = err or f"Coupon {coupon.code} applied."
    data = calc_totals(services, coupon, site.tax_percent)
    data.update(coupon_ok=bool(coupon), coupon_msg=msg, tax_percent=site.tax_percent)
    return jsonify(data)


@app.route("/book", methods=["GET", "POST"])
@login_required
def book():
    if request.method == "GET":
        selected = [int(x) for x in request.args.getlist("service") if x.isdigit()]
        return render_template("book.html", categories=ServiceCategory.query.order_by(ServiceCategory.id).all(),
                               selected=selected, pre_date=request.args.get("date", ""),
                               max_date=date.today() + timedelta(days=90))

    ids = [int(x) for x in request.form.getlist("services") if x.isdigit()]
    back = redirect(url_for("book", service=ids))
    services = Service.query.filter(Service.id.in_(ids), Service.active.is_(True)).all() if ids else []
    if not services or len(services) != len(set(ids)):
        flash("Please choose at least one available service.", "danger")
        return back
    staff = db.session.get(Staff, to_int(request.form.get("staff_id")))
    if not staff or not staff.active or not all(s in staff.services for s in services):
        flash("Please choose a staff member who offers the selected services.", "danger")
        return back
    day = parse_date(request.form.get("date"))
    if not day or day < date.today() or day > date.today() + timedelta(days=90):
        flash("Please choose a valid date within the next 90 days.", "danger")
        return back
    phone = clean(request.form.get("phone"), 20)
    if not PHONE_RE.match(phone):
        flash("Please enter a valid phone number.", "danger")
        return back

    site = SalonSettings.get()
    start = to_int(request.form.get("start"), -1)
    subtotal = round(sum(s.final_price for s in services), 2)
    coupon = None
    code = clean(request.form.get("coupon"), 30)
    if code:
        coupon, err = check_coupon(code, subtotal)
        if err:
            flash(err, "danger")
            return back
    totals = calc_totals(services, coupon, site.tax_percent)

    with BOOK_LOCK:   # re-check the slot right before saving, so two people can't take the same time
        if start not in get_slots(staff, day, totals["duration"]):
            flash("Sorry, that time was just taken or is not available. Please pick another time.", "danger")
            return back
        appt = Appointment(user_id=current_user.id, staff=staff, date=day, start_min=start,
                           duration=totals["duration"], subtotal=totals["subtotal"], discount=totals["discount"],
                           tax=totals["tax"], total=totals["total"], coupon_code=coupon.code if coupon else "",
                           notes=clean(request.form.get("notes"), 500), status="Pending")
        appt.services = services
        db.session.add(appt)
        db.session.flush()
        appt.booking_no = f"SPA-{day.year}-{appt.id:06d}"
        current_user.phone = phone
        db.session.commit()

    send_email_async(current_user.email, f"Booking received: {appt.booking_no}",
                     f"Hi {current_user.name},\n\nWe received your booking {appt.booking_no} for {appt.service_names} "
                     f"with {staff.name} on {day:%d %b %Y} at {appt.time_label}.\nTotal: Rs. {appt.total:,.2f}\n\n"
                     f"We'll confirm it shortly.\n{site.salon_name}")
    flash(f"Booking {appt.booking_no} created. Choose how you'd like to pay.", "success")
    return redirect(url_for("checkout", appt_id=appt.id))


@app.route("/checkout/<int:appt_id>", methods=["GET", "POST"])
@login_required
def checkout(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    mine_or_admin(appt.user_id)
    site = SalonSettings.get()
    outstanding = appt.remaining
    open_ok = appt.status in ACTIVE_STATUSES and outstanding > 0
    advance = min(outstanding, round(appt.total * site.advance_percent / 100, 2)) if outstanding else 0
    if request.method == "POST" and open_ok:
        choice = request.form.get("choice")
        if choice == "salon":
            pay = Payment(appointment=appt, amount=outstanding, method="Pay at salon", kind="At salon")
        elif choice in ("full", "advance") and site.upi_id:
            amount = outstanding if choice == "full" or advance <= 0 else advance
            pay = Payment(appointment=appt, amount=amount, method="UPI",
                          kind="Full" if amount == outstanding else "Advance")
        else:
            flash("Please choose a payment option.", "danger")
            return redirect(url_for("checkout", appt_id=appt.id))
        db.session.add(pay)
        db.session.commit()
        if pay.method == "UPI":
            return redirect(url_for("pay", payment_id=pay.id))
        flash("Done! Please pay at the salon. We'll see you soon.", "success")
        return redirect(url_for("dashboard"))
    return render_template("checkout.html", a=appt, outstanding=outstanding, advance=advance, open_ok=open_ok)


@app.route("/pay/<int:payment_id>", methods=["GET", "POST"])
@login_required
def pay(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    mine_or_admin(payment.appointment.user_id)
    if payment.method != "UPI":
        abort(404)
    site = SalonSettings.get()
    editable = payment.status in ("Pending", "Failed", "Submitted")
    if request.method == "POST":
        if not editable:
            abort(403)
        utr = re.sub(r"\s", "", request.form.get("utr", "")).upper()
        if not re.fullmatch(r"[A-Z0-9]{8,30}", utr):
            flash("Enter the UTR / transaction ID shown in your UPI app (8-30 letters or digits).", "danger")
            return redirect(url_for("pay", payment_id=payment.id))
        if Payment.query.filter(Payment.utr == utr, Payment.id != payment.id).first():
            flash("That transaction ID has already been used for another payment.", "danger")
            return redirect(url_for("pay", payment_id=payment.id))
        try:
            proof = save_image(request.files.get("proof"), Config.PRIVATE_FOLDER, "proof_")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("pay", payment_id=payment.id))
        payment.utr = utr
        if proof:
            payment.proof = proof
        payment.status = "Submitted"     # never "Verified": only an admin can do that
        db.session.commit()
        flash("Thanks! Your payment details were submitted. The salon will verify them shortly.", "success")
        return redirect(url_for("dashboard"))
    link = upi_link(site.upi_id, site.salon_name, payment.amount, payment.appointment.booking_no) if site.upi_id else ""
    return render_template("pay.html", p=payment, editable=editable, upi_uri=link)


@app.route("/qr/<int:payment_id>.png")
@login_required
def qr(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    mine_or_admin(payment.appointment.user_id)
    site = SalonSettings.get()
    if payment.method != "UPI" or not site.upi_id:
        abort(404)
    buf = upi_qr_png(site.upi_id, site.salon_name, payment.amount, payment.appointment.booking_no)
    return send_file(buf, mimetype="image/png")


@app.route("/proof/<int:payment_id>")
@login_required
def proof(payment_id):
    payment = db.session.get(Payment, payment_id) or abort(404)
    mine_or_admin(payment.appointment.user_id)
    if not payment.proof:
        abort(404)
    return send_file(os.path.join(Config.PRIVATE_FOLDER, os.path.basename(payment.proof)))


@app.route("/invoice/<int:appt_id>.pdf")
@login_required
def invoice(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    mine_or_admin(appt.user_id)
    buf = build_invoice(appt, SalonSettings.get())
    return send_file(buf, mimetype="application/pdf", as_attachment=request.args.get("dl") == "1",
                     download_name=f"invoice-{appt.booking_no}.pdf")


@app.route("/appointment/<int:appt_id>/cancel", methods=["POST"])
@login_required
def cancel_appointment(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    mine_or_admin(appt.user_id)
    if not appt.can_modify:
        flash("This appointment can no longer be cancelled online.", "danger")
    else:
        appt.status = "Cancelled"
        db.session.commit()
        note = " You've already paid, so the salon will contact you about a refund." if appt.paid_amount else ""
        flash(f"Appointment {appt.booking_no} cancelled.{note}", "info")
        notify_status(appt)
    return redirect(request.referrer if current_user.is_admin and request.referrer else url_for("dashboard"))


@app.route("/appointment/<int:appt_id>/reschedule", methods=["GET", "POST"])
@login_required
def reschedule(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    mine_or_admin(appt.user_id)
    if not appt.can_modify:
        flash("This appointment can't be rescheduled.", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        day, start = parse_date(request.form.get("date")), to_int(request.form.get("start"), -1)
        with BOOK_LOCK:
            if not day or start not in get_slots(appt.staff, day, appt.duration, exclude_id=appt.id):
                flash("That time isn't available. Please pick another.", "danger")
                return redirect(url_for("reschedule", appt_id=appt.id))
            appt.date, appt.start_min, appt.status = day, start, "Rescheduled"
            db.session.commit()
        notify_status(appt)
        flash(f"Appointment moved to {day:%d %b %Y} at {appt.time_label}.", "success")
        return redirect(url_for("admin_appointments") if current_user.is_admin else url_for("dashboard"))
    return render_template("reschedule.html", a=appt, max_date=date.today() + timedelta(days=90))


@app.route("/review/<int:appt_id>", methods=["GET", "POST"])
@login_required
def review(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    if appt.user_id != current_user.id:
        abort(404)
    if appt.status != "Completed" or appt.review:
        flash("You can review a completed appointment once.", "info")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        rating = to_int(request.form.get("rating"))
        if not 1 <= rating <= 5:
            flash("Please choose a rating from 1 to 5.", "danger")
        else:
            db.session.add(Review(user_id=current_user.id, appointment_id=appt.id, rating=rating,
                                  text=clean(request.form.get("text"), 600)))
            db.session.commit()
            flash("Thank you! Your review will appear once the salon approves it.", "success")
            return redirect(url_for("dashboard"))
    return render_template("review_form.html", a=appt)


def notify_status(appt):
    site = SalonSettings.get()
    send_email_async(appt.customer.email, f"Booking {appt.booking_no}: {appt.status}",
                     f"Hi {appt.customer.name},\n\nYour booking {appt.booking_no} ({appt.service_names}) on "
                     f"{appt.date:%d %b %Y} at {appt.time_label} is now: {appt.status}.\n\n{site.salon_name}\n{site.phone}")


# ======================= ADMIN =======================
@app.route("/admin")
@admin_required
def admin_dashboard():
    today = date.today()
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "Verified").scalar()
    stats = {
        "customers": User.query.filter_by(is_admin=False).count(),
        "today": Appointment.query.filter(Appointment.date == today, Appointment.status.in_(ACTIVE_STATUSES + ("Completed",))).count(),
        "upcoming": Appointment.query.filter(Appointment.date >= today, Appointment.status.in_(ACTIVE_STATUSES)).count(),
        "completed": Appointment.query.filter_by(status="Completed").count(),
        "revenue": revenue,
        "pending_payments": Payment.query.filter(Payment.status.in_(("Pending", "Submitted"))).count(),
    }
    todays = Appointment.query.filter(Appointment.date == today, Appointment.status.in_(ACTIVE_STATUSES)) \
        .order_by(Appointment.start_min).all()
    to_verify = Payment.query.filter_by(status="Submitted").order_by(Payment.id).limit(8).all()
    recent = Appointment.query.order_by(Appointment.id.desc()).limit(8).all()
    return render_template("admin/dashboard.html", stats=stats, todays=todays, to_verify=to_verify, recent=recent)


@app.route("/admin/appointments")
@admin_required
def admin_appointments():
    q = Appointment.query.join(User, Appointment.user_id == User.id)
    status = request.args.get("status", "")
    if status in STATUSES:
        q = q.filter(Appointment.status == status)
    day = parse_date(request.args.get("date"))
    if day:
        q = q.filter(Appointment.date == day)
    term = clean(request.args.get("q"), 50)
    if term:
        q = q.filter(or_(Appointment.booking_no.ilike(f"%{term}%"), User.name.ilike(f"%{term}%")))
    items = q.order_by(Appointment.date.desc(), Appointment.start_min.desc()).limit(200).all()
    return render_template("admin/appointments.html", items=items, status=status, day=request.args.get("date", ""), term=term)


@app.route("/admin/appointments/<int:appt_id>/status", methods=["POST"])
@admin_required
def admin_appointment_status(appt_id):
    appt = db.session.get(Appointment, appt_id) or abort(404)
    status = request.form.get("status")
    if status not in STATUSES:
        abort(400)
    appt.status = status
    db.session.commit()
    notify_status(appt)
    flash(f"{appt.booking_no} marked {status}.", "success")
    return redirect(request.referrer or url_for("admin_appointments"))


@app.route("/admin/customers")
@admin_required
def admin_customers():
    term = clean(request.args.get("q"), 50)
    q = User.query.filter_by(is_admin=False)
    if term:
        q = q.filter(or_(User.name.ilike(f"%{term}%"), User.email.ilike(f"%{term}%"), User.phone.ilike(f"%{term}%")))
    return render_template("admin/customers.html", items=q.order_by(User.id.desc()).all(), term=term)


@app.route("/admin/customers/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_customer_toggle(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.is_admin:
        abort(403)
    user.active = not user.active
    db.session.commit()
    flash(f"{user.name} is now {'active' if user.active else 'deactivated'}.", "success")
    return redirect(url_for("admin_customers"))


@app.route("/admin/customers/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_customer_delete(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.is_admin:
        abort(403)
    if user.appointments:
        flash("This customer has appointments, so it can't be deleted. Deactivate the account instead.", "warning")
    else:
        Review.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        flash("Customer deleted.", "success")
    return redirect(url_for("admin_customers"))


# ---- services & categories ----
@app.route("/admin/services")
@admin_required
def admin_services():
    return render_template("admin/services.html", categories=ServiceCategory.query.order_by(ServiceCategory.id).all())


@app.route("/admin/categories", methods=["POST"])
@admin_required
def admin_category_add():
    name = clean(request.form.get("name"), 80)
    icon = clean(request.form.get("icon"), 40) or "bi-stars"
    if not name or ServiceCategory.query.filter_by(name=name).first():
        flash("Enter a new, unique category name.", "danger")
    else:
        db.session.add(ServiceCategory(name=name, icon=icon if icon.startswith("bi-") else "bi-stars"))
        db.session.commit()
        flash("Category added.", "success")
    return redirect(url_for("admin_services"))


@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(cat_id):
    cat = db.session.get(ServiceCategory, cat_id) or abort(404)
    if cat.services:
        flash("Move or delete the services in this category first.", "warning")
    else:
        db.session.delete(cat)
        db.session.commit()
        flash("Category deleted.", "success")
    return redirect(url_for("admin_services"))


@app.route("/admin/services/new", methods=["GET", "POST"])
@app.route("/admin/services/<int:service_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_service_form(service_id=None):
    service = (db.session.get(Service, service_id) or abort(404)) if service_id else None
    categories = ServiceCategory.query.order_by(ServiceCategory.id).all()
    if request.method == "POST":
        f = request.form
        name, desc = clean(f.get("name"), 120), clean(f.get("description"), 2000)
        price, duration = to_float(f.get("price"), -1), to_int(f.get("duration"))
        disc = to_float(f.get("discount_price"), 0) or None
        cat = db.session.get(ServiceCategory, to_int(f.get("category_id")))
        error = None
        if not name or not cat:
            error = "Name and category are required."
        elif price <= 0 or not 15 <= duration <= 480:
            error = "Enter a price above 0 and a duration between 15 and 480 minutes."
        elif disc is not None and not 0 < disc < price:
            error = "Discount price must be lower than the regular price."
        if not error:
            try:
                image = save_image(request.files.get("image"), Config.UPLOAD_FOLDER, "svc_")
            except ValueError as exc:
                error = str(exc)
        if error:
            flash(error, "danger")
        else:
            if not service:
                service = Service()
                db.session.add(service)
            service.name, service.description, service.category = name, desc, cat
            service.price, service.discount_price, service.duration = price, disc, duration
            service.active = f.get("active") == "on"
            if image:
                service.image = image
            elif not service.image:
                service.image = cat.image
            db.session.commit()
            flash("Service saved.", "success")
            return redirect(url_for("admin_services"))
    return render_template("admin/service_form.html", s=service, categories=categories)


@app.route("/admin/services/<int:service_id>/delete", methods=["POST"])
@admin_required
def admin_service_delete(service_id):
    service = db.session.get(Service, service_id) or abort(404)
    if service.appointments:
        service.active = False
        flash("This service has past bookings, so it was hidden instead of deleted.", "warning")
    else:
        db.session.delete(service)
        flash("Service deleted.", "success")
    db.session.commit()
    return redirect(url_for("admin_services"))


# ---- staff ----
@app.route("/admin/staff")
@admin_required
def admin_staff():
    return render_template("admin/staff.html", items=Staff.query.order_by(Staff.id).all())


@app.route("/admin/staff/new", methods=["GET", "POST"])
@app.route("/admin/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_staff_form(staff_id=None):
    member = (db.session.get(Staff, staff_id) or abort(404)) if staff_id else None
    categories = ServiceCategory.query.order_by(ServiceCategory.id).all()
    if request.method == "POST":
        f = request.form
        name, email, phone = clean(f.get("name"), 100), clean(f.get("email"), 120), clean(f.get("phone"), 20)
        days = sorted({d for d in f.getlist("days") if d in DAY_KEYS})
        start, end = f.get("start_time", ""), f.get("end_time", "")
        error = None
        if not name:
            error = "Name is required."
        elif email and not EMAIL_RE.match(email):
            error = "Enter a valid email or leave it empty."
        elif phone and not PHONE_RE.match(phone):
            error = "Enter a valid phone number or leave it empty."
        elif not days:
            error = "Choose at least one working day."
        elif not (re.fullmatch(r"\d\d:\d\d", start) and re.fullmatch(r"\d\d:\d\d", end) and start < end):
            error = "Working hours are invalid (start must be before end)."
        if not error:
            try:
                photo = save_image(request.files.get("photo"), Config.UPLOAD_FOLDER, "staff_")
            except ValueError as exc:
                error = str(exc)
        if error:
            flash(error, "danger")
        else:
            if not member:
                member = Staff()
                db.session.add(member)
            member.name, member.email, member.phone = name, email, phone
            member.designation = clean(f.get("designation"), 100)
            member.working_days, member.start_time, member.end_time = ",".join(days), start, end
            member.active = f.get("active") == "on"
            if photo:
                member.photo = photo
            ids = [int(x) for x in f.getlist("services") if x.isdigit()]
            member.services = Service.query.filter(Service.id.in_(ids)).all() if ids else []
            db.session.commit()
            flash("Staff member saved.", "success")
            return redirect(url_for("admin_staff"))
    return render_template("admin/staff_form.html", m=member, categories=categories)


@app.route("/admin/staff/<int:staff_id>/delete", methods=["POST"])
@admin_required
def admin_staff_delete(staff_id):
    member = db.session.get(Staff, staff_id) or abort(404)
    if member.appointments:
        member.active = False
        flash("This staff member has bookings, so they were set to inactive instead of deleted.", "warning")
    else:
        db.session.delete(member)
        flash("Staff member deleted.", "success")
    db.session.commit()
    return redirect(url_for("admin_staff"))


# ---- payments ----
@app.route("/admin/payments")
@admin_required
def admin_payments():
    status = request.args.get("status", "")
    q = Payment.query
    if status:
        q = q.filter_by(status=status)
    return render_template("admin/payments.html", items=q.order_by(Payment.id.desc()).limit(200).all(), status=status)


@app.route("/admin/payments/<int:payment_id>/<action>", methods=["POST"])
@admin_required
def admin_payment_action(payment_id, action):
    payment = db.session.get(Payment, payment_id) or abort(404)
    new_status = {"verify": "Verified", "reject": "Failed", "refund": "Refunded"}.get(action)
    if not new_status:
        abort(404)
    payment.status = new_status
    payment.verified_at = datetime.now() if new_status == "Verified" else None
    appt = payment.appointment
    if new_status == "Verified" and appt.status == "Pending":
        appt.status = "Confirmed"                    # admin verified the money, so confirm the booking
    db.session.commit()
    if new_status == "Verified":
        send_email_async(appt.customer.email, f"Payment received for {appt.booking_no}",
                         f"Hi {appt.customer.name},\n\nWe verified your payment of Rs. {payment.amount:,.2f} "
                         f"for booking {appt.booking_no}. Thank you!")
    flash(f"Payment marked {new_status}.", "success")
    return redirect(request.referrer or url_for("admin_payments"))


# ---- coupons ----
@app.route("/admin/coupons", methods=["GET", "POST"])
@admin_required
def admin_coupons():
    if request.method == "POST":
        f = request.form
        code = re.sub(r"[^A-Za-z0-9_-]", "", f.get("code", "")).upper()[:30]
        kind, value = f.get("kind"), to_float(f.get("value"), -1)
        expiry = parse_date(f.get("expiry"))
        if not code or kind not in ("percent", "fixed") or value <= 0 or (kind == "percent" and value > 100):
            flash("Enter a code, a type and a valid value (percent must be 1-100).", "danger")
        elif Coupon.query.filter_by(code=code).first():
            flash("That code already exists.", "danger")
        else:
            db.session.add(Coupon(code=code, kind=kind, value=value, min_amount=max(to_float(f.get("min_amount")), 0),
                                  expiry=expiry, active=True))
            db.session.commit()
            flash("Coupon created.", "success")
        return redirect(url_for("admin_coupons"))
    return render_template("admin/coupons.html", items=Coupon.query.order_by(Coupon.id.desc()).all())


@app.route("/admin/coupons/<int:coupon_id>/<action>", methods=["POST"])
@admin_required
def admin_coupon_action(coupon_id, action):
    coupon = db.session.get(Coupon, coupon_id) or abort(404)
    if action == "toggle":
        coupon.active = not coupon.active
    elif action == "delete":
        db.session.delete(coupon)
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("admin_coupons"))


# ---- reviews ----
@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    return render_template("admin/reviews.html", items=Review.query.order_by(Review.id.desc()).all())


@app.route("/admin/reviews/<int:review_id>/<action>", methods=["POST"])
@admin_required
def admin_review_action(review_id, action):
    item = db.session.get(Review, review_id) or abort(404)
    if action == "approve":
        item.status = "Approved"
    elif action == "hide":
        item.status = "Hidden"
    elif action == "delete":
        db.session.delete(item)
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("admin_reviews"))


# ---- gallery ----
@app.route("/admin/gallery", methods=["GET", "POST"])
@admin_required
def admin_gallery():
    if request.method == "POST":
        try:
            name = save_image(request.files.get("image"), Config.UPLOAD_FOLDER, "gal_")
            if not name:
                raise ValueError("Please choose an image to upload.")
            db.session.add(Gallery(title=clean(request.form.get("title"), 100), image=name))
            db.session.commit()
            flash("Image uploaded.", "success")
        except ValueError as exc:
            flash(str(exc), "danger")
        return redirect(url_for("admin_gallery"))
    return render_template("admin/gallery.html", items=Gallery.query.order_by(Gallery.id.desc()).all())


@app.route("/admin/gallery/<int:item_id>/<action>", methods=["POST"])
@admin_required
def admin_gallery_action(item_id, action):
    item = db.session.get(Gallery, item_id) or abort(404)
    if action == "toggle":
        item.approved = not item.approved
    elif action == "delete":
        delete_file(Config.UPLOAD_FOLDER, item.image)
        db.session.delete(item)
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("admin_gallery"))


# ---- settings & holidays ----
@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    site = SalonSettings.get()
    if request.method == "POST":
        f = request.form
        upi = clean(f.get("upi_id"), 80)
        open_t, close_t = f.get("open_time", ""), f.get("close_time", "")
        tax, adv = to_float(f.get("tax_percent"), -1), to_float(f.get("advance_percent"), -1)
        error = None
        if not clean(f.get("salon_name"), 100):
            error = "Salon name is required."
        elif upi and not UPI_RE.match(upi):
            error = "UPI ID looks wrong. It should look like name@bank."
        elif not (re.fullmatch(r"\d\d:\d\d", open_t) and re.fullmatch(r"\d\d:\d\d", close_t) and open_t < close_t):
            error = "Opening time must be before closing time."
        elif not (0 <= tax <= 100 and 0 <= adv <= 100):
            error = "Tax and advance percentages must be between 0 and 100."
        elif f.get("email") and not EMAIL_RE.match(f.get("email", "").strip()):
            error = "Enter a valid salon email."
        if not error:
            try:
                logo = save_image(request.files.get("logo"), Config.UPLOAD_FOLDER, "logo_")
            except ValueError as exc:
                error = str(exc)
        if error:
            flash(error, "danger")
        else:
            site.salon_name, site.tagline = clean(f.get("salon_name"), 100), clean(f.get("tagline"), 200)
            site.about, site.address = clean(f.get("about"), 1000), clean(f.get("address"), 250)
            site.phone, site.email = clean(f.get("phone"), 30), clean(f.get("email"), 120)
            site.upi_id, site.open_time, site.close_time = upi, open_t, close_t
            site.weekly_off = ",".join(sorted({d for d in f.getlist("weekly_off") if d in DAY_KEYS}))
            site.tax_percent, site.advance_percent = tax, adv
            if logo:
                site.logo = logo
            db.session.commit()
            flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    holidays = Holiday.query.filter(Holiday.date >= date.today()).order_by(Holiday.date).all()
    return render_template("admin/settings.html", holidays=holidays)


@app.route("/admin/holidays", methods=["POST"])
@admin_required
def admin_holiday_add():
    day = parse_date(request.form.get("date"))
    if not day or Holiday.query.filter_by(date=day).first():
        flash("Choose a date that isn't already a holiday.", "danger")
    else:
        db.session.add(Holiday(date=day, reason=clean(request.form.get("reason"), 120)))
        db.session.commit()
        flash("Holiday added.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/holidays/<int:holiday_id>/delete", methods=["POST"])
@admin_required
def admin_holiday_delete(holiday_id):
    item = db.session.get(Holiday, holiday_id) or abort(404)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("admin_settings"))


# ---- reports ----
def csv_safe(value):
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text   # blocks spreadsheet formula injection


def month_range(month_text):
    try:
        first = datetime.strptime(month_text, "%Y-%m").date()
    except (TypeError, ValueError):
        first = date.today().replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt


@app.route("/admin/reports")
@admin_required
def admin_reports():
    first, nxt = month_range(request.args.get("month"))
    by_day, by_month = {}, {}
    for p in Payment.query.filter_by(status="Verified").all():
        when = (p.verified_at or p.created_at).date()
        by_month[when.strftime("%Y-%m")] = by_month.get(when.strftime("%Y-%m"), 0) + p.amount
        if first <= when < nxt:
            by_day[when.day] = by_day.get(when.day, 0) + p.amount
    months, cursor = [], date.today().replace(day=1)
    for _ in range(6):
        months.append((cursor.strftime("%b %Y"), by_month.get(cursor.strftime("%Y-%m"), 0)))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()
    month_appts = Appointment.query.filter(Appointment.date >= first, Appointment.date < nxt).all()
    status_counts = {s: sum(1 for a in month_appts if a.status == s) for s in STATUSES}
    popular = db.session.query(Service.name, func.count(Appointment.id).label("n")) \
        .join(appointment_services, appointment_services.c.service_id == Service.id) \
        .join(Appointment, Appointment.id == appointment_services.c.appointment_id) \
        .filter(Appointment.status != "Cancelled").group_by(Service.id).order_by(func.count(Appointment.id).desc()).limit(8).all()
    pay_counts = dict(db.session.query(Payment.status, func.count(Payment.id)).group_by(Payment.status).all())
    return render_template("admin/reports.html", month=first.strftime("%Y-%m"), month_label=first.strftime("%B %Y"),
                           by_day=sorted(by_day.items()), months=months, month_total=sum(by_day.values()),
                           booking_count=len(month_appts), status_counts=status_counts, popular=popular, pay_counts=pay_counts)


@app.route("/admin/reports/export.csv")
@admin_required
def admin_export():
    first, nxt = month_range(request.args.get("month"))
    rows = Appointment.query.filter(Appointment.date >= first, Appointment.date < nxt) \
        .order_by(Appointment.date, Appointment.start_min).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Booking no", "Customer", "Phone", "Services", "Staff", "Date", "Time", "Status",
                     "Total", "Paid", "Remaining", "Payment status"])
    for a in rows:
        writer.writerow([csv_safe(x) for x in (a.booking_no, a.customer.name, a.customer.phone, a.service_names,
                                               a.staff.name, a.date, a.time_label, a.status, a.total,
                                               a.paid_amount, a.remaining, a.payment_status)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=bookings-{first:%Y-%m}.csv"})


# ======================= START =======================
def init_db():
    with app.app_context():
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        os.makedirs(Config.PRIVATE_FOLDER, exist_ok=True)
        db.create_all()
        seed(Config.UPLOAD_FOLDER)
        if not User.query.filter_by(is_admin=True).first():
            print("\n  FIRST RUN: create your admin account at http://127.0.0.1:5000/setup")
            if Config.SETUP_KEY_FROM_ENV:
                print("  Use the SETUP_KEY from your .env file.\n")
            else:
                print(f"  Setup key (this run only): {Config.SETUP_KEY}\n")


init_db()

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
