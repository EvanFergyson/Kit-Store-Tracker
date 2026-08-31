# CUMC Kit Store Tracker

A Flask web app for Cardiff University Mountaineering Club to track kit (gear) inventory, sign equipment in/out, and let members request kit for trips.

## Features

- **Kit basket** (`/request` to browse, `/basket` to review & submit) — any member can browse kit by category/subcategory, add multiple items (and quantities) to a session-stored basket — like a web shop cart — then check out in one go by entering their name/email/notes. No login needed. "Can't find it?" adds a free-text item for anything not in the store yet.
- **Committee sign-in/out page** (`/sign-in-out`) — password-protected. Category/subcategory filters narrow down the item picker, then sign kit out to a borrower and sign it back in.
- **Admin page** (`/admin`) — password-protected. Manage categories and subcategories (two-level, e.g. Hardware → Single Rope), add/edit/delete kit items, and review requests grouped by the basket they were submitted in — with an "Approve All / Deny All" shortcut, or resolve items individually. Each item's purchase date is only ever shown here, not on the public or sign-in/out pages.
- Single shared committee password (set in `.env`) gates the two committee pages.

> **Schema note:** kit items now belong to a category → subcategory (e.g. Hardware → Static Rope) instead of a free-text category, and the old "condition" field has been removed. `ItemRequest` also gained a `request_group` column so items from the same basket checkout are linked together. If you've already run the app once under an older schema, delete `instance/kitstore.db` and re-run `python seed_data.py` — SQLAlchemy's `create_all()` only creates missing tables, it doesn't migrate existing ones.

## Required tools

| Tool | Why | Check if installed | Install |
|---|---|---|---|
| Python 3.10+ | Runs the Flask app | `python3 --version` | macOS: `brew install python3` (needs [Homebrew](https://brew.sh)), or download from [python.org](https://www.python.org/downloads/) |
| pip | Installs Python packages | `pip3 --version` | Bundled with Python 3.10+ |
| git | Already used for this repo | `git --version` | macOS: `brew install git` or via Xcode Command Line Tools (`xcode-select --install`) |

Everything else (Flask, SQLAlchemy, etc.) is installed into a project-local virtual environment below — no extra system tools needed.

## Setup

```bash
cd "Kit Store Tracker"

# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your environment file
cp .env.example .env
```

Open `.env` and set:
- `SECRET_KEY` — generate one with `python -c "import secrets; print(secrets.token_hex(32))"`
- `COMMITTEE_PASSWORD` — the shared password committee members will log in with

```bash
# 4. (Optional) seed a few sample kit items
python seed_data.py

# 5. Run the app
flask --app app run --debug
```

Visit `http://127.0.0.1:5000`. The database (`instance/kitstore.db`) is created automatically on first run.

## Project structure

```
app.py              # Routes and app factory
config.py           # Reads settings from .env
models.py           # KitItem, SignOut, ItemRequest (SQLAlchemy models)
seed_data.py         # Optional sample data
migrate_to_postgres.py  # One-time copy of instance/kitstore.db into a fresh Postgres DB
templates/           # Jinja2 HTML templates
static/css/style.css
static/js/main.js
tests/               # pytest - CSRF + concurrency regression tests
requirements.txt
requirements-dev.txt # Adds pytest, for running tests/ locally
Procfile             # For deployment (gunicorn)
.env.example
```

## Running tests

A small pytest suite covers the two hardest-to-catch-by-clicking-around bugs: the CSRF protection on every form, and the stock/name race conditions fixed in `_create_signout`/`_name_exists`. It runs against a throwaway temp SQLite file, never the real `instance/kitstore.db`.

```bash
pip install -r requirements-dev.txt
pytest
```

## How it works

- **KitItem**: an item type in the store (e.g. "50m Dynamic Rope") with a total quantity and a live "available" count.
- **SignOut**: one record per loan. Created when committee signs an item out, updated with a timestamp when it's signed back in. Signing out decreases `quantity_available`; signing in restores it.
- **ItemRequest**: a request from a member via the public form. Starts `pending`; committee marks it `approved` or `denied` from the admin page.
- Committee auth is a single shared password kept in `.env`, compared with `secrets.compare_digest` and stored in the Flask session — no per-user accounts. Good enough for a small club; if you outgrow it later, swap in [Flask-Login](https://flask-login.readthedocs.io/) with individual accounts.

## Deploying for free

Since this targets free hosting, two easy options:

**Render.com (recommended)**
1. Push this repo to GitHub.
2. Create a new "Web Service" on Render, connect the repo.
3. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app`.
4. Add `SECRET_KEY` and `COMMITTEE_PASSWORD` as environment variables in Render's dashboard.
5. Render's free tier filesystem is ephemeral (the SQLite file resets on every redeploy/restart/spin-down), and Render's own free Postgres expires after 30 days. Instead, create a free [Neon](https://neon.tech) Postgres project (permanent, no card) and set its connection string as `DATABASE_URL` in Render — `config.py` already supports this. If you already have data in `instance/kitstore.db`, run `python migrate_to_postgres.py "<neon-connection-string>"` once, locally, to copy it all over before pointing Render at the new database (safe to re-run: refuses to touch a target that already has data).

**PythonAnywhere**
1. Upload the project (or `git clone` it in a Bash console).
2. Create a virtualenv and `pip install -r requirements.txt`.
3. Configure a new web app (Flask, manual config) pointing at `app.py`, set environment variables in the WSGI config file.
4. PythonAnywhere's free tier disk *is* persistent, so SQLite works fine there long-term.

## Notes for further development

- Forms use plain HTML + Flask, matching your existing HTML/CSS/JS experience — no JS framework required, but `static/js/main.js` is there if you want to add client-side interactivity (e.g. live search/filtering of the kit table).
- All database logic lives in `models.py` / `app.py`, kept deliberately simple (no blueprints) so it's easy to extend as you learn more.
