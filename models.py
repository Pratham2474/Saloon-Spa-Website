from datetime import datetime, date, time
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

STATUSES = ["Pending", "Confirmed", "Completed", "Cancelled", "Rescheduled", "No-show"]
ACTIVE_STATUSES = ("Pending", "Confirmed", "Rescheduled")               # can still happen
BLOCKING_STATUSES = ("Pending", "Confirmed", "Rescheduled", "Completed")  # occupy staff time
PAYMENT_STATUSES = ["Pending", "Submitted", "Verified", "Failed", "Refunded"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Many-to-many link tables
staff_services = db.Table(
    "staff_services",
    db.Column("staff_id", db.Integer, db.ForeignKey("staff.id"), primary_key=True),
    db.Column("service_id", db.Integer, db.ForeignKey("service.id"), primary_key=True),
)
appointment_services = db.Table(
    "appointment_services",
    db.Column("appointment_id", db.Integer, db.ForeignKey("appointment.id"), primary_key=True),
    db.Column("service_id", db.Integer, db.ForeignKey("service.id"), primary_key=True),
)


class User(UserMixin, db.Model):
    """Customers and admins (is_admin=True)."""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    appointments = db.relationship("Appointment", back_populates="customer",
                                   order_by="Appointment.date.desc()")
    reviews = db.relationship("Review", back_populates="user")

    @property
    def is_active(self):  # Flask-Login: inactive users cannot log in
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ServiceCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    icon = db.Column(db.String(40), default="bi-stars")   # Bootstrap Icons class
    image = db.Column(db.String(200), default="")
    services = db.relationship("Service", back_populates="category", order_by="Service.id")

    @property
    def active_services(self):
        return [s for s in self.services if s.active]


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("service_category.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    duration = db.Column(db.Integer, nullable=False, default=60)   # minutes
    price = db.Column(db.Float, nullable=False, default=0)
    discount_price = db.Column(db.Float, nullable=True)
    image = db.Column(db.String(200), default="")
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    category = db.relationship("ServiceCategory", back_populates="services")
    staff_members = db.relationship("Staff", secondary=staff_services, back_populates="services")
    appointments = db.relationship("Appointment", secondary=appointment_services, back_populates="services")

    @property
    def final_price(self):
        return self.discount_price if self.discount_price else self.price

    @property
    def has_discount(self):
        return bool(self.discount_price) and self.discount_price < self.price


class Staff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    photo = db.Column(db.String(200), default="")
    phone = db.Column(db.String(20), default="")
    email = db.Column(db.String(120), default="")
    designation = db.Column(db.String(100), default="")
    working_days = db.Column(db.String(20), default="0,1,2,3,4,5")   # Monday=0 ... Sunday=6
    start_time = db.Column(db.String(5), default="10:00")
    end_time = db.Column(db.String(5), default="19:00")
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    services = db.relationship("Service", secondary=staff_services, back_populates="staff_members")
    appointments = db.relationship("Appointment", back_populates="staff")

    @property
    def days_list(self):
        return [int(x) for x in (self.working_days or "").split(",") if x.strip().isdigit()]


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_no = db.Column(db.String(20), unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, index=True)
    start_min = db.Column(db.Integer, nullable=False)        # minutes after midnight (600 = 10:00)
    duration = db.Column(db.Integer, nullable=False)         # total minutes
    subtotal = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    tax = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    coupon_code = db.Column(db.String(30), default="")
    status = db.Column(db.String(20), default="Pending", nullable=False)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.now)

    customer = db.relationship("User", back_populates="appointments")
    staff = db.relationship("Staff", back_populates="appointments")
    services = db.relationship("Service", secondary=appointment_services, back_populates="appointments")
    payments = db.relationship("Payment", back_populates="appointment",
                               order_by="Payment.id.desc()", cascade="all, delete-orphan")
    review = db.relationship("Review", back_populates="appointment", uselist=False)

    @staticmethod
    def label(minutes):
        h, m = divmod(minutes, 60)
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

    @property
    def time_label(self):
        return self.label(self.start_min)

    @property
    def end_label(self):
        return self.label(self.start_min + self.duration)

    @property
    def starts_at(self):
        return datetime.combine(self.date, time(self.start_min // 60, self.start_min % 60))

    @property
    def can_modify(self):
        return self.status in ACTIVE_STATUSES and self.starts_at > datetime.now()

    @property
    def service_names(self):
        return ", ".join(s.name for s in self.services)

    @property
    def paid_amount(self):
        return round(sum(p.amount for p in self.payments if p.status == "Verified"), 2)

    @property
    def remaining(self):
        return max(round(self.total - self.paid_amount, 2), 0)

    @property
    def payment_status(self):
        if self.paid_amount >= self.total - 0.01 and self.total > 0:
            return "Paid"
        if any(p.status == "Submitted" for p in self.payments):
            return "Awaiting verification"
        if self.paid_amount > 0:
            return "Partially paid"
        if any(p.status == "Pending" for p in self.payments):
            return "Pending"
        return "Unpaid"


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(20), default="UPI")         # UPI / Pay at salon
    kind = db.Column(db.String(20), default="Full")          # Full / Advance / At salon
    status = db.Column(db.String(20), default="Pending")
    utr = db.Column(db.String(40), default="")
    proof = db.Column(db.String(200), default="")            # file in PRIVATE_FOLDER
    created_at = db.Column(db.DateTime, default=datetime.now)
    verified_at = db.Column(db.DateTime, nullable=True)

    appointment = db.relationship("Appointment", back_populates="payments")


class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    kind = db.Column(db.String(10), default="percent")       # percent / fixed
    value = db.Column(db.Float, nullable=False)
    min_amount = db.Column(db.Float, default=0)
    expiry = db.Column(db.Date, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    def discount_for(self, subtotal):
        amount = subtotal * self.value / 100 if self.kind == "percent" else self.value
        return round(min(amount, subtotal), 2)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.id"), unique=True, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, default="")
    status = db.Column(db.String(10), default="Pending")     # Pending / Approved / Hidden
    created_at = db.Column(db.DateTime, default=datetime.now)

    user = db.relationship("User", back_populates="reviews")
    appointment = db.relationship("Appointment", back_populates="review")


class Gallery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), default="")
    image = db.Column(db.String(200), nullable=False)
    approved = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    reason = db.Column(db.String(120), default="")


class SalonSettings(db.Model):
    """A single row that holds every admin-editable setting."""
    id = db.Column(db.Integer, primary_key=True)
    salon_name = db.Column(db.String(100), default="Glow & Grace Spa & Salon")
    tagline = db.Column(db.String(200), default="Hair, beauty and spa treatments, booked in a minute.")
    about = db.Column(db.Text, default="Glow & Grace is a neighbourhood spa and salon. Our therapists and stylists "
                                       "work one client at a time, so your appointment is never rushed.")
    logo = db.Column(db.String(200), default="")
    address = db.Column(db.String(250), default="12 Rosewood Plaza, Pune, Maharashtra 411001")
    phone = db.Column(db.String(30), default="+91 98765 43210")
    email = db.Column(db.String(120), default="hello@glowandgrace.example")
    upi_id = db.Column(db.String(80), default="")
    open_time = db.Column(db.String(5), default="10:00")
    close_time = db.Column(db.String(5), default="20:00")
    weekly_off = db.Column(db.String(20), default="1")       # comma list, Monday=0
    tax_percent = db.Column(db.Float, default=0)
    advance_percent = db.Column(db.Float, default=30)

    @property
    def off_days(self):
        return [int(x) for x in (self.weekly_off or "").split(",") if x.strip().isdigit()]

    @staticmethod
    def get():
        s = SalonSettings.query.first()
        if not s:
            s = SalonSettings()
            db.session.add(s)
            db.session.commit()
        return s
