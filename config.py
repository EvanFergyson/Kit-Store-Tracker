import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Flask session signing key. MUST be overridden in production via .env
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Shared password committee members use to access sign-in/out + admin pages
    COMMITTEE_PASSWORD = os.environ.get("COMMITTEE_PASSWORD", "changeme")

    # SQLite by default; can be swapped for Postgres etc. via DATABASE_URL
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'kitstore.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
