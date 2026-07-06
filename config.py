import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Flask session signing key. MUST be overridden in production via .env
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Shared password committee members use to access sign-in/out + admin pages
    COMMITTEE_PASSWORD = os.environ.get("COMMITTEE_PASSWORD", "changeme")

    # SQLite by default; can be swapped for Postgres etc. via DATABASE_URL.
    # Render (and formerly Heroku) hand out connection strings starting
    # "postgres://", but SQLAlchemy 1.4+ only accepts the "postgresql://"
    # scheme - rewrite it here so the same DATABASE_URL just works instead of
    # failing with a cryptic "could not parse SQLAlchemy URL" on deploy.
    _database_url = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'kitstore.db')}"
    )
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
