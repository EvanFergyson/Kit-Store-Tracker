# CUMC Kit Store Tracker — Usability & Black-Box Test Report

**Date:** 4 July 2026
**Scope:** Four use-case flows (two per persona) tested against a live local instance of the app. No code was changed as part of this exercise.

---

## Fixes applied (4 July 2026, follow-up pass)

Acted on the findings below. Kept the red branding throughout, just tuned the shade for contrast.

| Finding | Status | What changed |
|---|---|---|
| Requests ↔ Sign-out disconnect (High) | **Fixed** | `ItemRequest` now has a `fulfilled_signout_id` link. Approved requests show a "Mark as Collected" button (only when the item name matches something in the catalog and enough stock is available); clicking it creates a real `SignOut`, decrements stock, and the row shows a "Collected" badge from then on. Custom/"other" requests that don't match a catalog item show a note to sign out manually instead. |
| Add-to-basket full page reload | **Fixed** | "Add to Basket", quantity updates, and removals on the basket page now submit over `fetch()` and show a floating toast instead of reloading — scroll position on the browse page is preserved. Falls back to a normal form submit automatically if JavaScript fails for any reason. |
| Header nav / button contrast (~3.9–4.4:1) | **Fixed** | Swapped which red is "primary": buttons and the header now use the darker `#bc1016` (~6.5:1 with white text) instead of `#ec1d25`, which is kept as an accent colour (used for the focus ring). Still unmistakably the same red. |
| Success flash/badge text contrast (~4.4:1) | **Fixed** | Darkened `--color-success` slightly (now ~5.7:1). |
| No visible keyboard-focus style | **Improved** | Added an on-brand focus ring for links, buttons, and form fields (this wasn't in the original report but came up while touching the CSS). |
| Borrower name is free text | **Improved** | Sign-out form now autocompletes from previously-used borrower names via a `<datalist>`, so repeat loans to the same person are easier to enter consistently — doesn't stop someone typing a new name. |
| No loan history / overdue tracking, inline category rename, non-inline form validation | **Deferred** | Left as-is; these are lower severity and are reasonable candidates for a later pass rather than this one. |

---

## Methodology

Each flow was executed against a running copy of the Flask app in an isolated environment, using scripted HTTP requests (form submissions, redirects, cookies) to reproduce exactly what a browser sends when a person clicks through the site — this is what makes it "black box": the app was exercised the same way, without reading its internals to decide what to click next. Every step below records what was actually returned (status code, redirect target, page content, flash message) rather than what the code is expected to do.

One honest limitation: this sandbox has no display and no working headless browser (Playwright/Chromium could be installed but not launched — the container lacks the system libraries and there's no permission to install them), so nothing here is a visual screenshot. The UI/UX judgements below are based on reading the actual rendered HTML and the CSS values shipped in `style.css`, plus manual colour-contrast maths, rather than looking at a picture of the page. If you want a true pixel-level check, the fastest way is to just click through it yourself in a browser (or ask me to compare with Claude in Chrome, which can drive your actual browser but can't reach this sandbox's server).

Also worth flagging: `static/css/style.css` currently uses a red brand colour (`--color-primary: #ec1d25`), not the green it was originally built with — someone (you, presumably) changed it since I last touched the file. The analysis below reflects the file as it stands now.

## Personas

- **Club member** — no login, just wants to get kit for a trip with minimal friction.
- **Committee member** — logs in with the shared password, handles day-to-day sign-outs and reviews incoming requests.

---

## Flow CM-1: Club member requests kit for a weekend trip (happy path)

**Goal:** Browse the store, add two items from different categories to a basket, and submit a request.

| # | Step | Result |
|---|---|---|
| 1 | Load home page `/` | 200 OK |
| 2 | Load `/request`, browse table | 200 OK — 7 kit rows rendered, category/subcategory filters present |
| 3 | Add "50m x 9.8mm Dynamic Rope" (qty 1) to basket | 302 → redirected back to referring page |
| 4 | Add "4-Person Tent" (qty 2) to basket | 302, nav badge updates to "Basket (3)" |
| 5 | Open `/basket` | 200 OK, both lines shown with correct quantities |
| 6 | Submit checkout form with no name entered | Rejected — "Please enter your name", basket contents preserved |
| 7 | Submit again with name/email/notes filled in | 302 → `/request`, flash "Your request has been sent to the committee" |
| 8 | Reload `/basket` | Empty-state message shown; confirmed in the database the two lines were written as separate `ItemRequest` rows sharing one `request_group` id |

**Result: Pass.** Every step behaved correctly, including the important negative case (missing name blocks submission without losing the basket).

**Observations:**
- Every "Add to Basket" click is a full page reload back to the referring page, not an in-place update. On a long item list this resets scroll position — if a member is halfway down a 40-item Hardware list and adds something, they land back at the top of the page and have to scroll down again to keep browsing. Amazon, and most cart-based sites, add to the cart without leaving the page (a toast/mini-cart update) for exactly this reason.
- There's no visible basket preview until you click through to `/basket` — just a number in the nav. A small dropdown/tooltip preview (as most e-commerce sites do) would let a member sanity-check what they've added without a full navigation.
- Nothing on the browse page tells a member roughly how long the committee usually takes to respond, or what happens next — minor, but it's the kind of reassurance checkout flows usually give ("we'll email you within X days").

---

## Flow CM-2: Club member edits their basket and handles edge cases

**Goal:** Add a real item and a "not listed" custom item, adjust quantities, remove one, and probe a few edge cases a curious or careless user might trigger.

| # | Step | Result |
|---|---|---|
| 1 | Add "60m Static Rope" (qty 1) | 302, added |
| 2 | Add custom item "Bivvy bag (personal)" via the "Can't find it?" form | 302, added |
| 3 | Update the custom item's quantity to 3 | 302, quantity updated correctly |
| 4 | Remove the custom item | 302, confirmed gone from `/basket` |
| 5 | POST `/basket/add` with `quantity=0` | Silently clamped to 1 server-side (no crash, no visible warning) |
| 6 | POST `/basket/add` with `quantity=-5` | Same — clamped to 1 |
| 7 | POST `/basket/add` with neither `item_id` nor `custom_name` | Rejected with flash "Choose an item or describe what you need" |
| 8 | POST to remove an already-removed basket key | No error, silently ignored |
| 9 | POST to update a basket key that was never added (`item:99999`) | No error, silently ignored |
| 10 | POST to a malformed basket key (`garbage`, no colon) | No error, silently ignored |

**Result: Pass**, with caveats. Nothing crashed or produced a server error (HTTP 500) under any of these adversarial inputs, which is a real strength — the basket logic is defensively written.

**Observations:**
- In practice, a real browser session can't submit `quantity=0` because the `<input type="number" min="1">` blocks it client-side. The only way to reach that code path is a direct API call (as I did here), so this is low real-world risk — but it's worth knowing the server-side floor exists and silently corrects rather than rejecting, in case a future feature exposes it more directly (e.g., a JS-driven quantity stepper that doesn't respect `min`).
- All validation errors and success messages depend on a full page reload to appear (Flask's flash-message pattern). There's no inline, same-page feedback (e.g., a red outline appearing under a field the instant you leave it blank). This matches a fairly old-school web form pattern; most current apps validate inline before you ever hit submit.

---

## Flow CB-1: Committee member signs kit out and back in

**Goal:** Log in, sign an item out to a borrower, verify availability updates, then sign it back in.

| # | Step | Result |
|---|---|---|
| 1 | Load `/sign-in-out` while logged out | 302 → `/login?next=/sign-in-out` (correct redirect-with-return-path) |
| 2 | Submit wrong password | Page reloads with "Incorrect password", no session granted |
| 3 | Submit correct password | 302 → `/sign-in-out` (honours the `next` param) |
| 4 | Load `/sign-in-out` | 200 OK, filters present, "Nothing is currently signed out" shown correctly with an empty log |
| 5 | Sign out 2x Climbing Helmet to "Jordan (member)" with a note | 302, success flash |
| 6 | Reload page | Availability dropped from 10/10 to 8/10; "Jordan (member)" appears in the active table with the note |
| 7 | Attempt to sign out 999x the same helmet | Rejected — "Only 8 of 'Climbing Helmet' available" |
| 8 | Sign the original loan back in | 302, success flash |
| 9 | Reload page | Availability restored to 10/10, active table back to empty state |
| 10 | Log out, then try to load `/admin` | 302 → `/login?next=/admin` — session correctly cleared |

**Result: Pass.** This is the strongest flow in the app — auth, the availability arithmetic, and the over-draw guard all behaved exactly as a librarian/gear-store checkout system should. This matches the standard of dedicated equipment-booking tools (e.g. university library equipment loan systems), which is the right comparison point here rather than a general e-commerce site.

**Observations:**
- No confirmation step before signing an item back in — one click, done. That's a reasonable trade-off for a low-risk, easily-reversible action a committee member does often; adding a confirm dialog here (like the delete buttons already have) would probably be annoying rather than helpful.
- The borrower name is a free-text field, not tied to a member list. Two entries for the same person with slightly different spelling ("Jordan", "jordan m", "Jordan (member)") would show up as different borrowers with no way to reconcile them later. Worth considering a simple autocomplete or a members table if the club wants to track loan history per person over time.

---

## Flow CB-2: Committee member processes a request and manages inventory

**Goal:** Approve/deny a member's basket request item-by-item, then add a new category, subcategory and kit item, and confirm the delete safeguards hold.

| # | Step | Result |
|---|---|---|
| 1 | (Setup) Member submits a basket with 2 items ("Trangia Stove", "Walking Poles (Pair)") and a note | Request recorded, grouped under one `request_group` |
| 2 | Committee logs in, opens `/admin` | Pending section shows the group: requester name, email, timestamp, note, both items listed, "Approve All / Deny All" buttons present because the group has >1 item |
| 3 | Approve the stove individually | 302, moved to "Recently Resolved" with an "approved" badge |
| 4 | Deny the walking poles individually | 302, moved to "Recently Resolved" with a "denied" badge |
| 5 | Add new category "Electronics" | 302, appears in category list |
| 6 | Add new subcategory "Headtorches" under it | 302, appears grouped correctly |
| 7 | Add new item "Petzl Tikka" (qty 8, purchase date, location) under that subcategory | 302, item created |
| 8 | Attempt to delete the "Headtorches" subcategory while it still has an item in it | Blocked — "Cannot delete 'Headtorches' while kit items use it" |
| 9 | Sign one "Petzl Tikka" out, then attempt to delete the item | Blocked — "Cannot delete 'Petzl Tikka' while units are signed out" |
| 10 | Add a category named "Electronics" again (duplicate) | Blocked — "already exists" |
| 11 | Submit "Add Kit Item" with a blank name | Blocked — "Item name is required" |

**Result: Pass**, all guard rails held. **One real gap found, not a crash but a workflow hole:**

**⚠️ Approving a request does not touch stock or create a sign-out record.** I confirmed this directly: after approving the Trangia Stove request, its availability was still 6/6 — completely unchanged. The "Requests" system (members asking for things) and the "Sign In/Out" system (committee tracking what's physically out) are two entirely disconnected data models. In practice this means:
- A committee member could approve a request for an item that's already fully checked out to someone else, with no warning.
- After approving, the committee member has to *remember* to separately go to Sign In/Out and manually create the loan when the member actually collects the gear — re-typing the same name and item a second time.
- There's no link on the admin page from an approved request to the resulting sign-out, so a few days later there's no way to tell "did this approved request ever actually get collected?"

This is the single most impactful finding in this report — everything else is polish, this is a workflow gap that will cause real confusion once the club is using it for genuine trips.

---

## Cross-cutting UI/UX comparison to similar apps

**Basket/checkout (club member side) vs. e-commerce carts (Amazon, any Shopify store):** structurally very close — browse, add multiple things with quantities, review, one checkout form. What's missing relative to that standard is the stuff that makes carts feel responsive: no AJAX add-to-basket (full reload every time), no mini-cart preview, no "added!" confirmation near the button you just clicked (the flash banner appears at the top of the page, outside the visible area if you've scrolled down a long list).

**Sign-in/out (committee side) vs. equipment/library loan systems (e.g. university sports-kit or AV-equipment booking tools):** this is the better comparison, and the app holds up well against it — clear "currently out" table, over-draw prevention, simple return action. Most systems in this category also show *loan history per item* (who's had it, how often) and *overdue flags* (expected return date passed) — neither exists here yet, which is worth knowing since it's a common expectation once the club has been using the system for a term or two.

**Admin/category management vs. lightweight admin panels (Trello board admin, Google Forms responses view):** the grouped request view mirrors how Google Forms groups multi-question submissions per respondent, which is a good instinct. The category/subcategory manager is more basic than comparable tools — no drag-to-reorder, no inline rename (you can only add or delete, not edit a name in place), and deleting requires a JS `confirm()` popup rather than a proper "are you sure" page, which is fine for a small club tool but is the oldest possible pattern for destructive actions.

## Accessibility notes (source-derived, not a live scan)

Approximate WCAG contrast ratios calculated from the current CSS custom properties:

- White text on the primary red button (`#ffffff` on `#ec1d25`): **~4.4:1** — just under the 4.5:1 AA threshold for normal text, though it clears the 3:1 threshold that applies to bold/large button text, so this is a minor, not urgent, issue.
- Header nav links (`#eef3ef` on `#ec1d25`): **~3.9:1** — below the 4.5:1 AA threshold for regular-weight body text. This is the one contrast pairing worth a second look if accessibility matters to the club (e.g. members with low vision).
- Flash success text (`#2f7d4f` on `#e4f4e9`): **~4.4:1** — again just under AA for small text.
- Flash error text and muted label text both comfortably clear AA (~5.6:1 and ~5.7:1 respectively).

Structurally, the forms are in good shape: every input has an associated `<label for>`, required fields use the native `required` attribute (so screen readers and browsers both announce them), and buttons use real `<button>` elements rather than styled `<div>`s. No images are used, so there are no missing alt-text concerns.

## Summary of findings

| Area | Severity | Finding |
|---|---|---|
| Requests ↔ Sign-out disconnect | **High** | Approving a request never reduces stock or creates a loan record — two separate manual steps with no link between them |
| Add-to-basket UX | Low–Medium | Full page reload per add; no mini-cart preview; scroll position lost on long lists |
| Contrast: header nav text | Low | ~3.9:1, below AA for body text |
| Contrast: primary button / success flash text | Low | ~4.4:1, marginal under AA |
| No loan history / overdue tracking | Low (future) | Common in comparable equipment-booking tools, absent here |
| Borrower name is free text | Low | No member list/autocomplete, so name variants fragment history |
| Category/subcategory admin | Low | No inline rename, delete uses browser `confirm()` |
| Form validation | Low | Purely server-round-trip; no inline/same-page validation |

## Conclusion

Functionally, all four flows work correctly, including every edge case and guard rail I tried to break — no crashes, no data corruption, sensible error messages throughout. The biggest thing worth your attention before this goes in front of real members is the gap between the request/approval flow and the sign-out flow, since that's the part that will actually cause confusion in day-to-day committee use. Everything else here is refinement rather than a functional problem.
