import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY") or os.urandom(32).hex()
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///khayal.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30
