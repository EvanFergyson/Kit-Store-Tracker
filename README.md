# CUMC Kit Store Tracker

A Flask web app for Cardiff University Mountaineering Club to track kit (gear) inventory, sign equipment in/out, and let members request kit for trips.

## Features

- **Kit basket** (`/request` to browse, `/basket` to review & submit) — any member can browse kit by category/subcategory, add multiple items (and quantities) to a session-stored basket — like a web shop cart — then check out in one go by entering their name/email/notes. No login needed. "Can't find it?" adds a free-text item for anything not in the store yet.
- **Committee sign-in/out page** (`/sign-in-out`) — password-protected. Category/subcategory filters narrow down the item picker, then sign kit out to a borrower and sign it back in.
- **Colour-coded racks** — trad racks (Red, Blue, ...) are signed out and back in as one unit from Sign In/Out; their pieces can't be lent on their own, and a rack can't go out while any piece is out or flagged as damaged. Set them up under Admin › Racks.
- **Due dates and reminders** — every sign-out has a return date (default 7 days), and online requests get email receipts and reminders (see Deploying).
- **Admin page** (`/admin`) — password-protected. Manage categories and subcategories (two-level, e.g. Hardware → Single Rope), add/edit/delete kit items, and review requests grouped by the basket they were submitted in — with an "Approve All / Deny All" shortcut, or resolve items individually. Each item's purchase date is only ever shown here, not on the public or sign-in/out pages.
- Single shared committee password (set in `.env`) gates the two committee pages.

> **Schema note:** kit items now belong to a category → subcategory (e.g. Hardware → Static Rope) instead of a free-text category, and the old "condition" field has been removed. `ItemRequest` also gained a `request_group` column so items from the same basket checkout are linked together. If you've already run the app once under an older schema, delete `instance/kitstore.db` and re-run `python seed_data.py` — SQLAlchemy's `create_all()` only creates missing tables, it doesn't migrate existing ones.

## Required tools

| Tool | Why | Check if installed | Install |
|---|---|---|---|
| Python 3.14 | Runs the Flask app (matches `.python-version`, which Render uses) | `python3 --version` | macOS: `brew install python3` (needs [Homebrew](https://brew.sh)), or download from [python.org](https://www.python.org/downloads/) |
| pip | Installs Python packages | `pip3 --version` | Bundled with Python |
| git | Already used for this repo | `git --version` | macOS: `brew install git` or via Xcode Command Line Tools (`xcode-select --install`) |

Everything else (Flask, SQLAlchemy, etc.) is installed into a project-local virtual environment below — no extra system tools needed.

## Setup

```bash
cd "Kit Store Tracker"

# 1. Create and activate a virtual environment
python3.14 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

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
4. Add `SECRET_KEY`, `COMMITTEE_PASSWORD` and `GEAR_PASSWORD` as environment variables in Render's dashboard (the app refuses to start without them). For reminder emails, also add the variables under **Reminder emails** below.
5. Render's free tier filesystem is ephemeral (the SQLite file resets on every redeploy/restart/spin-down), and Render's own free Postgres expires after 30 days. Instead, create a free [Neon](https://neon.tech) Postgres project (permanent, no card) and set its connection string as `DATABASE_URL` in Render — `config.py` already supports this. If you already have data in `instance/kitstore.db`, run `python migrate_to_postgres.py "<neon-connection-string>"` once, locally, to copy it all over before pointing Render at the new database (safe to re-run: refuses to touch a target that already has data).

**Reminder emails**

Borrowers whose kit was collected from an online request get a receipt, a reminder the day before it's due, and an overdue reminder on the due date and every 3 days after. The Gear Secretary gets a Monday digest of everything overdue (including walk-in loans with no email).

1. Create a free [Resend](https://resend.com) account and verify the club's sending domain (Resend only emails your own address until a domain is verified).
2. Set in Render: `RESEND_API_KEY`, `MAIL_FROM` (e.g. `CUMC Kit Store <reminders@cumc.co.uk>`), `GEAR_SEC_EMAIL`, optionally `REQUESTS_EMAIL` (comma-separated addresses told about new member requests; defaults to `GEAR_SEC_EMAIL`), optionally `MAIL_REPLY_TO` (where member replies go; defaults to `GEAR_SEC_EMAIL`), and `CRON_SECRET` (any long random string — `python -c "import secrets; print(secrets.token_hex(32))"`).
3. Render's free service sleeps when idle, so something outside has to trigger the reminders. Create a free job at [cron-job.org](https://cron-job.org) that runs **hourly** and sends `POST https://<your-app>.onrender.com/tasks/send-reminders` with the header `X-Cron-Secret: <your CRON_SECRET>`. Running it more often is harmless: every email is logged and only ever sent once.

Without `RESEND_API_KEY` (e.g. locally) emails are only logged, never sent. Without `CRON_SECRET` the endpoint is switched off (404).

**PythonAnywhere**
1. Upload the project (or `git clone` it in a Bash console).
2. Create a virtualenv and `pip install -r requirements.txt`.
3. Configure a new web app (Flask, manual config) pointing at `app.py`, set environment variables in the WSGI config file.
4. PythonAnywhere's free tier disk *is* persistent, so SQLite works fine there long-term.

## Gear register

The Gear Secretary's register of serial-numbered kit lives at `/gear` (also linked from the admin tabs) behind its own `GEAR_PASSWORD`.

- **Settings:** switch on serial tracking for safety-critical subcategories and set their manufacturer lifespan in years.
- **Add Piece / Import:** register pieces one at a time, or upload a CSV. Columns: `item_name, serial, brand, manufacture_date, purchase_date, condition, notes` (download the template from Import / Export). The file is checked and previewed first; nothing is saved until you confirm.
- **Manufacture dates** are decoded from the serial where the brand's format is known (Petzl, DMM, Wild Country and Mammut; Black Diamond, Beal and Edelrid print the date on the label instead). Codes that only give a month or year decode to the 1st of the month / 1 January, so lifespan warnings err on the early side, and always shown for you to check against the label. To add another brand, add a rule to `serial_dates.py` with real sample serials in `tests/test_gear.py`.
- **Inspections:** record Good / Minor wear / Needs attention / Retire with notes. A piece marked Needs attention or Retire blocks its rack from being signed out; Retire can also take it out of stock.
- **Export** the whole register as CSV for a backup.

## Notes for further development

- Forms use plain HTML + Flask, matching your existing HTML/CSS/JS experience — no JS framework required, but `static/js/main.js` is there if you want to add client-side interactivity (e.g. live search/filtering of the kit table).
- All database logic lives in `models.py` / `app.py`, kept deliberately simple (no blueprints) so it's easy to extend as you learn more.
