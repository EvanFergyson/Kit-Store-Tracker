# CLAUDE.md

Flask app for Cardiff University Mountaineering Club (CUMC) to track kit inventory. Members browse kit and submit requests via a session basket; committee signs kit in/out and manages inventory. Solo project, not live yet — target deployment is Render (web) + Neon (Postgres). README.md covers setup and deploy steps; don't duplicate it here.

## Commands
- Venv: `source .venv/bin/activate`
- Run locally: `flask --app app run --debug` — debug relaxes the SECRET_KEY/COMMITTEE_PASSWORD requirement and the Secure cookie
- Tests: `pytest` (install `requirements-dev.txt` first). Uses a temp SQLite file, never `instance/kitstore.db`
- Seed sample data: `python seed_data.py`
- One-off SQLite → Postgres copy: `python migrate_to_postgres.py "<url>"` (refuses a non-empty target)

## Working rules
- Run `pytest` after every code change and report the result.
- Never commit or push unless asked.
- Target Python 3.14 (`.python-version`, which Render reads). Anything added to `requirements.txt` must install on 3.14.
- Comments: light — only where something is genuinely non-obvious. Don't imitate the long why-comments in older code, but don't strip them either.
- Keep it flat: routes in `app.py` inside `register_routes`, models in `models.py`, plain Jinja templates, vanilla CSS/JS in `static/`. No blueprints, JS frameworks, or new heavy dependencies without asking.

## Architecture
- `app.py`: `create_app()` → `db.create_all()` + `_ensure_schema()` → `register_routes(app)`. Module-level `app = create_app()` is what gunicorn and tests import.
- `config.py`: loads `.env`; refuses to start without SECRET_KEY + COMMITTEE_PASSWORD + GEAR_PASSWORD unless FLASK_DEBUG is set; rewrites `postgres://` → `postgresql://`.
- `models.py`:
  - Category → Subcategory → KitItem; optional ItemGroup ties size variants together.
  - KitBundle / KitBundleLine: preset kit lists; a line points at an item or a group.
  - SignOut: one row per loan (`signed_in_at` null while out).
  - ItemRequest: member request; items from one basket checkout share `request_group`; status pending/approved/denied; `fulfilled_signout_id` links to the resulting SignOut.
  - Rack / RackLoan: colour-coded rack; `KitItem.rack_id` makes an item rack-only. A RackLoan creates one SignOut per piece (`SignOut.rack_loan_id`).
  - GearUnit / Inspection: gear register (serials, condition history), only for subcategories with `track_serials`.
  - EmailLog: one row per reminder email sent, so the hourly cron run never repeats one.
- Auth: single shared committee password, `@login_required`, in-memory per-IP lockout (`_login_attempts`). No user accounts. The gear register (`/gear`) has its own `GEAR_PASSWORD` and `@gear_required`; committee login doesn't unlock it.
- Emails: `send_email` posts to Resend via urllib (no SDK); without `RESEND_API_KEY` it appends to `OUTBOX`. `run_reminders(today)` is triggered by an external cron hitting `/tasks/send-reminders` with `X-Cron-Secret`.
- `serial_dates.py`: pure manufacture-date decoders per brand. Only add a brand with real sample serials in the tests.
- Member basket lives in the Flask session (`get_basket`, `basket_line_items`).
- `purchase_date` is admin-only — never show it on member or sign-in/out pages.

## Invariants — don't break these
- CSRF: every POST form includes `{{ csrf_field() }}`. `tests/test_csrf.py` only checks rejection without a token, so new forms won't be caught automatically.
- Stock: sign-outs must go through `_create_signout`, whose single UPDATE…WHERE `quantity_available >= quantity` prevents races; sign-ins through `_return_signout`; total changes through `_set_item_total`. Never change `quantity_available` via read-modify-write in Python.
- Racks are all-or-nothing: rack pieces must stay out of member pages, per-item/group sign-out and bundles (filter `rack_id IS NULL`; `ItemGroup.active_items` already excludes them).
- Names: use `_name_exists` for friendly validation, backed by DB-level unique constraints; `IntegrityError`/`SQLAlchemyError` have global handlers.
- Schema: `create_all()` never alters existing tables. Any new column or constraint change needs an idempotent step in `_ensure_schema()` that works on both SQLite and Postgres.
- Single gunicorn worker (`Procfile`): the login lockout and `_ensure_schema` both assume it. Flag it if scaling workers.
- Parse form input with `parse_quantity` / `parse_positive_int` / `parse_date`, not bare `int()`.

## Frontend
- `templates/base.html`: layout + SVG icon sprite (reuse via `<use href="#icon-x">`). Admin pages share `_admin_nav.html`.
- `static/css/style.css`: design tokens as CSS variables in `:root` — reuse them, don't hard-code hex. `--color-primary: #bc1016` was chosen for WCAG AA contrast.
- Mobile matters: committee uses the sign-in/out pages on phones.

## Current priorities
1. Getting it live on Render + Neon (env vars, domain).
2. UI polish / mobile.
3. New member and committee features.
