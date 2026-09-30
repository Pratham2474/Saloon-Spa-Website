"""Optional email. If MAIL_SERVER is empty nothing is sent and nothing breaks."""
import os
import smtplib
import threading
from email.message import EmailMessage


def send_email(to, subject, body):
    server = os.environ.get("MAIL_SERVER")
    if not server or not to:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("MAIL_USERNAME") or "noreply@localhost"
        msg["To"] = to
        msg.set_content(body)
        port = int(os.environ.get("MAIL_PORT", "587"))
        use_ssl = os.environ.get("MAIL_USE_SSL", "0") == "1"
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(server, port, timeout=10) as smtp:
            if not use_ssl and os.environ.get("MAIL_USE_TLS", "1") == "1":
                smtp.starttls()
            if os.environ.get("MAIL_USERNAME"):
                smtp.login(os.environ["MAIL_USERNAME"], os.environ.get("MAIL_PASSWORD", ""))
            smtp.send_message(msg)
        return True
    except Exception as exc:          # never let email problems break a booking
        print("Email not sent:", exc)
        return False


def send_email_async(to, subject, body):
    threading.Thread(target=send_email, args=(to, subject, body), daemon=True).start()
