import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

_DEV_SECRET_KEY = "dev-secret-key-change-me"
_DEV_COMMITTEE_PASSWORD = "changeme"


class Config:
    # Set FLASK_DEBUG=1 for local development only. It enables both Flask's
    # debug mode (interactive Werkzeug debugger + auto-reload) and the
    # relaxed fallback secrets below. Never set this in production - the
    # debugger lets anyone who can trigger a 500 run arbitrary Python.
    DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")

    # Flask session signing key. Falls back to a known, publicly-visible
    # value only when DEBUG is on; outside of that, startup fails loudly
    # instead of silently shipping a forgeable session key (see the check
    # below) - this used to fall back silently everywhere.
    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET_KEY if DEBUG else None)

    # Shared password committee members use to access sign-in/out + admin
    # pages. Same reasoning as SECRET_KEY above.
    COMMITTEE_PASSWORD = os.environ.get("COMMITTEE_PASSWORD", _DEV_COMMITTEE_PASSWORD if DEBUG else None)

    if not DEBUG and (SECRET_KEY is None or COMMITTEE_PASSWORD is None):
        raise RuntimeError(
            "SECRET_KEY and COMMITTEE_PASSWORD must both be set via environment "
            "variables when FLASK_DEBUG is not set. Refusing to start with a "
            "guessable default outside local development - either set both in "
            "your environment/.env, or set FLASK_DEBUG=1 for local dev only."
        )

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

    # Session cookie hardening. HttpOnly/SameSite are safe unconditionally
    # (nothing in this app needs JS to read the cookie or a cross-site POST
    # to carry it). Secure requires the deployment to actually be HTTPS-only
    # - true on Render - so it follows DEBUG the same way SECRET_KEY does:
    # only relaxed for local plain-HTTP development.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = not DEBUG
