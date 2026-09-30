import os
import secrets
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _secret_key():
    """Use SECRET_KEY from .env, otherwise create one once and keep it in .secret_key."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


class Config:
    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "salon.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")      # public images
    PRIVATE_FOLDER = os.path.join(BASE_DIR, "private_uploads")       # payment screenshots
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024                              # 5 MB uploads
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = None
    SETUP_KEY_FROM_ENV = bool(os.environ.get("SETUP_KEY"))
    SETUP_KEY = os.environ.get("SETUP_KEY") or secrets.token_urlsafe(8)
