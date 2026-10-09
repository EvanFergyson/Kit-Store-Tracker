import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

_DEV_SECRET_KEY = "dev-secret-key-change-me"
_DEV_COMMITTEE_PASSWORD = "changeme"
_DEV_GEAR_PASSWORD = "gearchangeme"


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

    # Separate password for the gear register (serials, inspections), kept by
    # the Gear Secretary - the shared committee login doesn't unlock it.
    GEAR_PASSWORD = os.environ.get("GEAR_PASSWORD", _DEV_GEAR_PASSWORD if DEBUG else None)

    if not DEBUG and (SECRET_KEY is None or COMMITTEE_PASSWORD is None or GEAR_PASSWORD is None):
        raise RuntimeError(
            "SECRET_KEY, COMMITTEE_PASSWORD and GEAR_PASSWORD must all be set via "
            "environment variables when FLASK_DEBUG is not set. Refusing to start "
            "with a guessable default outside local development - either set them "
            "in your environment/.env, or set FLASK_DEBUG=1 for local dev only."
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
    # Caps request bodies (the gear register CSV upload is the only big one).
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Default loan length used to pre-fill the return date on sign-out forms.
    DEFAULT_LOAN_DAYS = int(os.environ.get("DEFAULT_LOAN_DAYS", "7"))

    # Reminder emails (via Resend). With no RESEND_API_KEY, emails are kept in
    # an in-memory outbox and logged instead of sent - fine for local dev and
    # tests. CRON_SECRET must match the X-Cron-Secret header on the scheduled
    # call to /tasks/send-reminders; leave it unset to disable that endpoint.
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
    MAIL_FROM = os.environ.get("MAIL_FROM", "CUMC Kit Store <kit@example.org>")
    GEAR_SEC_EMAIL = os.environ.get("GEAR_SEC_EMAIL")
    # Where replies to reminder emails go (the sending address itself has no
    # inbox). Falls back to GEAR_SEC_EMAIL.
    MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO") or GEAR_SEC_EMAIL
    CRON_SECRET = os.environ.get("CRON_SECRET")
    OVERDUE_REMINDER_EVERY_DAYS = 3
    # Neon (like most hosted Postgres) drops idle connections; check each one
    # before use so the first request after a quiet spell doesn't error.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}

    # Session cookie hardening. HttpOnly/SameSite are safe unconditionally
    # (nothing in this app needs JS to read the cookie or a cross-site POST
    # to carry it). Secure requires the deployment to actually be HTTPS-only
    # - true on Render - so it follows DEBUG the same way SECRET_KEY does:
    # only relaxed for local plain-HTTP development.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = not DEBUG
