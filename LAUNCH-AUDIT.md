# Live site audit — cumc.co.uk

Read-only check of the production site as it stands right now, before real
users start finding their own issues. No login attempted (see note at the
bottom on why) and no POST requests sent, so nothing here touched production
data.

## Confirmed working

- **Domain + HTTPS**: `cumc.co.uk` and `www.cumc.co.uk` both resolve.
  `http://cumc.co.uk` correctly redirects to `https://`, so nobody can land
  on an unencrypted version by mistake.
- **Latest code is actually live**: the Hardwear category page shows the
  filter-chip redesign with all 13 real subcategories, matching what was
  built and tested - confirms the GitHub → Render deploy pipeline is
  working end to end.
- **Real data loaded correctly**: category counts on the Request Kit page
  (Bouldering 7, Cage 45, Hardwear 31, Other 21, Personal Safety 5, Soft
  Items 19 = 128 items) match the real inventory from `seed_data.py`.
- **Auth protection holds in production**: visiting `/admin` or
  `/sign-in-out` while logged out redirects to `/login?next=...` rather than
  leaking any committee-only content.
- **Error handling doesn't leak anything**: a bad URL and a nonexistent
  category ID both return a plain 404, not a debug traceback - confirms
  Flask's debug mode is correctly off in production (gunicorn never enables
  it, but good to see it verified live too).
- **No JavaScript errors** on any page visited (home, request, category,
  login, basket, 404).
- **Static assets cache correctly** - repeat requests to the CSS/JS files
  come back `304 Not Modified` instead of re-downloading.
- **Mobile rendering** looks right - the filter chips wrap cleanly, the nav
  and cards hold up at a 390px phone width.

## Issues found (all minor, all fixable)

1. **No favicon** - `/favicon.ico` returns 404. Cosmetic only (blank/default
   tab icon), but easy to add.
2. **Default 404 page is unbranded** - a mistyped URL shows Flask's bare
   "Not Found" text page instead of something with your site's header/nav,
   so anyone who fat-fingers a link has to hit back with no way to get
   home from that page.
3. **A few inline links render in default browser blue/underline** instead
   of your red branding - e.g. the "Browse kit" link on the empty basket
   page. No CSS rule currently targets plain links inside body text
   (`.empty-state a` and similar), so these stand out against the otherwise
   consistent styling everywhere else.

None of these are urgent - they're the kind of thing worth batching into a
small follow-up pass rather than fixing individually.

## What I couldn't check, and why

- **Security response headers and cookie flags** (HSTS, CSP,
  `X-Content-Type-Options`, whether the session cookie is marked
  `Secure`/`HttpOnly`) - my browser tooling blocks any inspection of
  cookie-related response data as a safety guardrail, and I don't have raw
  network access to your domain from my sandbox to check with curl either.
  From the code, `config.py` doesn't set these explicitly, so Flask/Render's
  defaults apply - worth a quick manual check (e.g. via your browser's own
  dev tools, or a site like securityheaders.com) since this app now handles
  a real login over the public internet.
- **True cold-start behaviour** - Render's free tier spins down after 15
  minutes idle, and the first visitor after that waits ~30-60s. I couldn't
  reproduce this because my own repeated requests kept the instance warm
  throughout this check.
- **The logged-in committee/admin experience** - I'm not able to enter the
  committee password myself, even with permission (that's a hard rule for
  me, not a judgement call). So this report only covers what a logged-out
  club member sees. The admin dashboard, add/edit item forms, sign-out flow
  and request approval were all tested against equivalent code in the
  sandbox before deployment, but I haven't verified them against the real
  production database. Worth doing a quick click-through yourself as the
  first real test - or log in and share a few screenshots if you want me to
  look over anything specific.
