# Deploying the Kit Store Tracker

This covers taking the app from your machine to a real website the committee
and club members can reach, using [Render](https://render.com) for hosting +
a managed Postgres database, plus a custom domain.

Render was chosen over alternatives (PythonAnywhere, Railway) because it's
the only one that gives you a free custom domain and a real Postgres add-on
without needing an expensive plan just to get both at once - see the cost
breakdown at the bottom for the numbers.

## 0. What's already in place

The codebase was already set up with deployment in mind, so most of the
plumbing exists:

- `Procfile` - tells any host how to start the app: `web: gunicorn app:app`
- `config.py` - reads `SECRET_KEY`, `COMMITTEE_PASSWORD` and `DATABASE_URL`
  from environment variables, falling back to local dev defaults
- `.env.example` - documents which environment variables production needs
- `.gitignore` - already excludes `.env` and the local SQLite file, so
  secrets and local data never get committed
- `requirements.txt` - now includes `psycopg2-binary`, the Postgres driver
- `app.py` calls `db.create_all()` on startup, so tables get created
  automatically the first time the app boots against a fresh database - no
  separate migration step needed
- `seed_data.py` is idempotent (it skips anything already in the database),
  so it's safe to run against production more than once

The one thing I changed for this: `config.py` now rewrites a `postgres://`
connection string to `postgresql://` if Render hands you the old-style
prefix, since newer SQLAlchemy versions only accept `postgresql://`. Without
this the app would fail to start with a cryptic parsing error the first time
you deployed.

I tested the Postgres path as far as I could without a live Postgres server
(compiled every table's schema under the Postgres dialect, confirmed the
driver loads and attempts a real connection) - everything checks out, but
this hasn't run against a real Postgres instance yet, so treat the first
deploy as the real test and watch the logs.

## 1. Push your changes to GitHub

Your repo is already connected to `github.com/EvanFergyson/Kit-Store-Tracker`.
I can't run git commands against your project folder directly, so from your
own terminal, in the project folder:

```
git add config.py requirements.txt
git commit -m "Add Postgres support for deployment"
git push
```

## 2. Create a Render account and a Postgres database

This part's yours to do (account creation isn't something I can do on your
behalf):

1. Go to [render.com](https://render.com) and sign up (GitHub login is
   easiest, since you'll be connecting a GitHub repo anyway).
2. In the dashboard, click **New +** → **PostgreSQL**.
3. Give it a name (e.g. `cumc-kit-store-db`), pick the free region closest to
   you, and create it.
4. The free Postgres instance is good for 30 days before it needs upgrading
   to a paid plan (see cost section) - fine for getting everything working
   first, but you'll want to upgrade before it expires so the data doesn't
   get deleted.
5. Once it's created, open it and copy the **Internal Database URL** - you'll
   paste this into the web service's environment variables in the next step.

## 3. Create the web service

1. Back in the dashboard, click **New +** → **Web Service**.
2. Connect your GitHub account and select the `Kit-Store-Tracker` repo.
3. Fill in:
   - **Build Command**: `pip install -r requirements.txt && python seed_data.py`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: Free, to start (see cost section for when you'd want
     to upgrade)

   Running `seed_data.py` as part of the build means the very first deploy
   loads the club's real inventory and current loan data straight from the
   spreadsheet-derived list already in the codebase. Because the script
   skips anything that already exists, it's harmless to leave this in the
   build command permanently - every future deploy just re-checks and skips.

4. Under **Environment**, add these three variables:
   - `SECRET_KEY` - generate one locally first:
     `python -c "import secrets; print(secrets.token_hex(32))"`
   - `COMMITTEE_PASSWORD` - the real password committee members will use to
     log in
   - `DATABASE_URL` - paste the Internal Database URL you copied in step 2
5. Click **Create Web Service**. Render will build and deploy - watch the
   logs for the `seed_data.py` output confirming categories/items/sign-outs
   were seeded, and for any startup errors.
6. Once it's live, you'll get a free `your-service-name.onrender.com` URL.
   Visit it and confirm the homepage, login and admin pages all work before
   moving on to the custom domain.

## 4. Point a custom domain at it

1. Buy a domain from any registrar (Namecheap, Google Domains, 123-reg, etc.)
   - this is a real purchase, so it's on you to pick one and pay for it.
2. In Render, open your web service → **Settings** → **Custom Domains** →
   **Add Custom Domain**, and enter your domain.
3. Render will show you the DNS records to add (usually a `CNAME` for a
   subdomain like `kitstore.cumc.co.uk`, or an `A`/`ALIAS` record for a bare
   domain). Add those records in your registrar's DNS settings.
4. DNS changes can take anywhere from a few minutes to a few hours to take
   effect. Render automatically issues a free HTTPS certificate for the
   domain once it verifies the DNS records.

## Cost summary

| Item | Free | Paid |
|---|---|---|
| Web service | Free, but spins down after 15 min idle (first visitor after that waits ~30-60s for it to wake up) | ~$7/month for an always-on instance |
| Postgres | Free for 30 days, then deleted unless upgraded | From ~$6/month (256MB, expandable) for a persistent instance |
| Custom domain (Render side) | Free - 2 domains included per workspace | - |
| Domain registration | - | ~£8-12/year, paid to your registrar, separate from Render |

Realistic minimum to keep this running permanently with real data and a
custom domain: **~$6/month** (Postgres) + your domain's yearly renewal. Add
the ~$7/month web service tier if the 30-60 second cold-start wake-up after
inactivity would bother committee members or people submitting requests.

## Day-to-day once it's live

- **Deploying updates**: every `git push` to `main` triggers a new deploy
  automatically - no separate step needed.
- **Logs**: the web service's **Logs** tab in the Render dashboard shows
  live output, useful for debugging anything that goes wrong after a deploy.
- **Changing the committee password**: update the `COMMITTEE_PASSWORD`
  environment variable in Render's dashboard and redeploy (or just save the
  env var change - Render restarts the service automatically).
- **Backups**: Render's paid Postgres plans include automatic daily backups;
  worth checking the current retention window on your plan before relying on
  it for anything irreplaceable.
