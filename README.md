# Glow & Grace Spa & Salon: booking website

Flask + SQLAlchemy + SQLite. Customers book online and pay by UPI QR; the admin verifies payments by hand.

## Run on Windows

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Open http://127.0.0.1:5000

(macOS/Linux: `source venv/bin/activate` and `cp .env.example .env`.)

The page loads Bootstrap, icons and fonts from a CDN, so you need internet access in the browser.

## First run

1. The database (`salon.db`) is created and filled with categories, 21 services, 5 staff, a coupon (`WELCOME10`) and sample gallery pictures.
2. The console prints a **setup key**. Open http://127.0.0.1:5000/setup, enter the key and create your admin account. This page stops working once an admin exists.
3. Log in, then go to **Admin > Settings** and add your **UPI ID**. Until you do, customers can only choose "Pay at salon".

Set `SETUP_KEY` in `.env` if you want a fixed key instead of a random one.

## How payment works

- Customer books, then picks **Pay full**, **Pay advance** (percentage from Settings) or **Pay at salon**.
- For UPI, a QR is generated from UPI ID + amount + booking number. The customer pays, then enters the UTR (and optionally a screenshot). Status becomes **Submitted**.
- Nothing is ever marked paid automatically. In **Admin > Payments**, check your bank/UPI app and click **Verify** or **Reject**. Verifying a payment also confirms a Pending booking.
- Screenshots are stored in `private_uploads/` and only the customer and admins can open them.

## How double booking is prevented

Free times are computed per staff member from salon hours, the person's own hours/days, holidays and their existing bookings (Pending, Confirmed, Rescheduled, Completed). A booking of 60 minutes at 10:00 blocks 10:00 and 10:30 for that person only. The slot is checked again inside a lock right before saving, so two people cannot take the same time.

## Files

```
app.py            routes (customer + admin)
config.py         settings, reads .env
models.py         database tables
utils/helpers.py  time slots, coupons, uploads, QR, PDF invoice
utils/email_utils.py  optional SMTP email
utils/seed.py     first-run sample data
templates/        HTML (admin pages in templates/admin)
static/           css, js, uploads (public images)
private_uploads/  payment screenshots (not public)
```

## Email (optional)

Fill the `MAIL_*` values in `.env`. If `MAIL_SERVER` is empty, nothing is sent and the site works normally.

## Security notes

Passwords are hashed, all forms use CSRF tokens, admin pages check the admin role, uploads are checked as real images and renamed, login is locked for 10 minutes after 5 failed attempts, and the secret key lives in `.env` or an auto-created `.secret_key` file. Do not commit `.env`, `.secret_key` or `salon.db`. For a public server, run behind HTTPS with a production server such as waitress, and set `FLASK_DEBUG=0`.

## Common changes

- Prices, services, staff, coupons, holidays: all from the admin panel.
- Slot length is 30 minutes: change `t += 30` in `get_slots` (`utils/helpers.py`).
- Colours: the variables at the top of `static/css/style.css`.
