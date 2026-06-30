# CUMC Kit Store Tracker

A Flask web app for Cardiff University Mountaineering Club to track kit (gear) inventory, sign equipment in/out, and let members request kit for trips.

## Features

- **Public request form** (`/request`) — any member can request an item, no login needed.
- **Committee sign-in/out page** (`/sign-in-out`) — password-protected. Sign kit out to a borrower, sign it back in, see what's currently out.
- **Admin page** (`/admin`) — password-protected. Add/edit/delete kit items, approve or deny member requests.
- Single shared committee password (set in `.env`) gates the two committee pages.

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
templates/           # Jinja2 HTML templates
static/css/style.css
static/js/main.js
requirements.txt
Procfile             # For deployment (gunicorn)
.env.example
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
5. Note: Render's free tier filesystem is ephemeral, so the SQLite file resets on redeploy. For a persistent store, add a free Render PostgreSQL instance and set `DATABASE_URL` instead (the app already supports this via `config.py`).

**PythonAnywhere**
1. Upload the project (or `git clone` it in a Bash console).
2. Create a virtualenv and `pip install -r requirements.txt`.
3. Configure a new web app (Flask, manual config) pointing at `app.py`, set environment variables in the WSGI config file.
4. PythonAnywhere's free tier disk *is* persistent, so SQLite works fine there long-term.

## Notes for further development

- Forms use plain HTML + Flask, matching your existing HTML/CSS/JS experience — no JS framework required, but `static/js/main.js` is there if you want to add client-side interactivity (e.g. live search/filtering of the kit table).
- All database logic lives in `models.py` / `app.py`, kept deliberately simple (no blueprints, no Flask-WTF) so it's easy to extend as you learn more.
