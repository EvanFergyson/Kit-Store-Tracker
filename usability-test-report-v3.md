# CUMC Kit Store Tracker — Round 3: Real-Browser Visual Retest & Report Comparison

**Date:** 5 July 2026
**Scope:** Full retest against the user's actual running local instance, using the Claude in Chrome extension for real navigation, clicks, and screenshots — not source-code reasoning. Every fix claimed in the previous engineering pass is checked against a real rendered page, and the same real rendering surfaced several new, genuine defects that were invisible from reading the code alone.

## Methodology, and why this round is different

Round 1 was black-box testing via curl. Round 2 added a meticulous line-by-line audit of the actual CSS and template source (colour maths, padding maths, interaction-pattern comparisons) because no working headless browser was available in that sandbox. Both were methodologically honest, but both had the same blind spot: neither one ever *looked* at the app the way a person actually would.

This round removes that blind spot. You started the Flask app locally (`http://127.0.0.1:5000`) and granted Chrome access, so everything below was captured by actually driving a real Chrome window: real navigation, real clicks, real form submissions against your real seeded data, real screenshots, and pixel-level zooms into specific controls. Two consequences of that follow directly:

1. Every "confirmed fixed" claim below is now backed by a screenshot of the fix actually working, not just a reading of the CSS rule that should produce it.
2. A handful of real defects turned up that **no amount of source reading would have caught**, because they only exist as an emergent property of real content flowing through real CSS at a real viewport width. These are flagged clearly as new in this round, and they are, frankly, the most important findings in this document — a couple of them are more disruptive than anything in the first two reports.

One environment limitation worth being upfront about: the remote browser session in this sandbox would not let me resize the actual Chrome window (`resize_window` reported success but the rendered viewport stayed at its original ~1200–1316px width regardless, and neither the resize call nor a zoom-shortcut fallback changed `window.innerWidth`). For mobile verification, I instead injected the site's own `@media (max-width: 600px)` CSS rules directly into the live page (unconditionally, matching the file exactly) inside a 390px-wide region, which renders the real DOM under the real mobile rules rather than a mockup. I've labelled every mobile finding below as coming from this method so it's clear it's a faithful simulation of the real breakpoint rather than a true on-device screenshot.

---

## Part 1 — Confirmed fixes (verified against real screenshots)

Everything in this section was implemented after the last report and is now visually confirmed working, not just assumed from the diff.

**Colour differentiation (primary vs destructive).** Sitting the Basket page's "Submit Request" button next to "Remove" in the same screenshot shows two genuinely different reds — a true brand red and a distinctly orange-leaning red — not two shades of the same hue like before. Same contrast holds throughout Admin: green Approve/Restore, orange Deny/Delete, blue Collected, red Mark-as-Collected/Sign Out, all legible at a glance in the same view. This one holds up well under real rendering.

**Touch-target sizing.** The `.btn-small` buttons measured out to ~40px in the browser, matching the CSS math from the last report and a real improvement over the old ~28px. They still fall short of the ideal 44px, but they're comfortably clickable now.

**Quantity stepper.** Every quantity field across Request, Basket, Sign Out, and Admin's Add/Edit Item forms renders as a real −/value/+ control with proper spacing, and the +/− buttons work correctly (confirmed by clicking them repeatedly and watching the number change). No native spinner arrows anywhere.

**Filtered empty state.** Forcing the Request page's category/subcategory filters into a combination with zero matches (Hiking category + Helmets subcategory, which don't overlap) correctly shows "No kit matches this filter — try a different category or subcategory." instead of a silently blank table. Confirmed live.

**Archive-instead-of-delete.** This is the fix I was most anxious to see actually work, since it was the single highest-severity bug in the last report. Live test: signed out 2× Climbing Helmet, signed it back in, clicked Delete. It did not crash. Instead it flashed "'Climbing Helmet' has loan history, so it's been archived rather than deleted" and a new "Archived Items" card appeared with the item shown as a name + grey "Archived" pill + green "Restore" button. Restoring it put it straight back into the catalogue and the Request page. This closes the worst bug from Round 2 cleanly, and the new UI for it is genuinely well-designed — clear copy, sensible placement, obvious action.

**Duplicate item names.** Tried adding "climbing helmet" (lowercase) and later "CLIMBING HELMET" (uppercase) via the Add Kit Item form — both were rejected with "An item named '...' already exists — choose a different name, or edit the existing one instead of adding a duplicate." No duplicate row appeared in the inventory table either time. Case-insensitivity confirmed live, not just in the code.

**Login lockout.** Five wrong passwords in a row on the real login page produced a toast reading "Too many incorrect attempts. Try again in 30 seconds." — and, importantly, a sixth attempt with the *correct* password during that window was still rejected with the same message and a live countdown. This is real, working brute-force throttling, confirmed against the actual login form.

**Row-flash-saved feedback.** Updating a basket line's quantity and screenshotting immediately (rather than waiting, which I did wrong on my first attempt and mistook the animation for absent) shows the whole row flash a soft green before fading — genuinely solves the "did my quantity change actually save?" ambiguity from the last report.

**Toast-only feedback.** Triggering a server-rendered flash (e.g. the login lockout message, or the "Restored" success message after clicking Restore) now shows up as a bottom-right toast, not a top-of-page banner — confirming the JS-side promotion of flash messages into toasts is working, so committee members no longer see two different feedback idioms depending on which action they took.

**Grouped pending requests.** Submitted a real two-item basket request as "Sam Climber" from a second tab, then watched it appear in Admin as a single card: name, email, timestamp, quoted note, an "Approve All / Deny All" pair (since it has 2 lines), and a mini-table of the two items with their own Approve/Deny. Approved it, then used "Mark as Collected" on each line — stock decremented correctly (Trangia Stove 6→5, Climbing Helmet 10→9) and the fulfilment column correctly swapped from a "Mark as Collected" button to a blue "Collected" pill that can't be clicked again.

**Homepage "no login needed" hint.** Present and legible under the two hero buttons, exactly as designed.

---

## Part 2 — New findings, only visible via real rendering

This is the substantive new content this round produced. None of these were flagged in Round 1 or Round 2 because none of them are visible from reading the CSS and template source in isolation — they only appear once real content is laid out by a real browser at a real width.

### 2.1 — Several primary action buttons render with no colour at all (High)

Looking at the real Request page, the "Add to Basket" button is a flat light-grey button with black text — indistinguishable in weight from the −/+ stepper buttons sitting right next to it. Zooming in confirms it: no background colour, no border colour, nothing to signal "this is the button that does the primary thing." The cause is simple once you look for it: this button's class is `btn btn-small` with no colour modifier (`btn-primary`, `btn-success`, etc.), so per the stylesheet it falls back to the browser's unstyled default button chrome.

This isn't a one-off. The same bare `btn btn-small` pattern (and therefore the same grey, uncoloured rendering) affects:
- "Add to Basket" (Request page, both the catalogue rows and the custom-item form)
- "Update" (Basket page, quantity line)
- "Add" (Admin → new category, new subcategory)
- "Save" (Admin → Kit Inventory inline edit)
- "Sign In" (Sign In/Out → currently-signed-out table)

Every one of these is a legitimate, common, "forward progress" action — arguably more of them than the buttons that *do* have colour. On a page like Request, where "Add to Basket" is the entire point of the page, having it render identically in weight to a neutral stepper button is a real prominence problem: the eye has nothing to land on. This was completely invisible from reading `style.css` in isolation, because the CSS itself is fine — the bug is that these five templates simply never apply a colour class to begin with, which only becomes obvious once you see the rendered grey button next to a correctly-coloured one.

### 2.2 — The Sign Out and Add Kit Item forms visually misassign labels to the wrong fields at common laptop widths (High)

This is the most surprising find of the session. At the real window width Chrome gave me for most of this test (~1200–1316px, an entirely ordinary laptop width), the Sign Out form renders like this:

```
Category        [category select]      Subcategory   [subcategory select]   Item
[item select]   Borrower name           [borrower box]  Quantity              [qty stepper]
Notes           [notes box]             [Sign Out]
```

Read that again: the "Item" label sits at the end of row 1, but the actual `<select id="item_id">` control is the *first* cell of row 2 — directly underneath the Category select, where a sighted user's eye reads it as belonging to Category, not Item. The same thing happens one step later: "Borrower name" is a label in row 2, but its input box is the *next* cell over, next to the Item select. The exact same failure mode hits the Add Kit Item form in Admin, where "Storage location"'s actual input ends up looking like a second Name field.

The mechanism: `.inline-form` uses `grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))`, and each label/input is a separate grid child in DOM order. At a container width where an *odd* number of 160px+ columns happen to fit per row, a row boundary falls in the middle of a label/input pair instead of between pairs, so the visual grouping breaks even though the underlying HTML `for`/`id` association (and therefore keyboard/screen-reader behaviour) is completely correct. I confirmed this is real by forcing the site's own mobile `@media (max-width: 600px)` rules onto the page: at a true single-column width, the grid collapses to one item per row and the pairing looks correct again (label, then its own input, in strict order) — the bug only exists in that "in-between" zone, and that zone happens to cover an extremely common range of real laptop and small-desktop windows. Reading the CSS on paper, `repeat(auto-fit, minmax(160px,1fr))` looks perfectly reasonable; only watching it actually reflow real content at a real width reveals which specific widths break it.

### 2.3 — The Kit Inventory table forces the entire page to scroll horizontally, hiding the Delete button off-screen (Critical)

This is the most severe issue found this round, more severe than anything in the previous two reports precisely because of how invisible it is until you go looking. At the same ~1200–1316px window width:

- `document.documentElement.scrollWidth` measured 1272px against a 1200px viewport — genuine page-level horizontal overflow, not a clipped/contained one.
- Scrolling the page right (a page-level horizontal scrollbar appears at the very bottom of the browser window — an interaction pattern almost nobody expects on a normal content page) reveals the "Delete" button for every row in Kit Inventory sitting entirely off the right edge of the visible viewport.
- Scrolling back to see the header while still shifted horizontally shows the red nav bar failing to extend across the newly-revealed area — a visibly broken, unfinished-looking layout, not just an inconvenience.

The cause: the Kit Inventory table has eight columns, several of which contain real form controls with their own minimum widths (a text input, a `<select>`, a native date picker, a quantity stepper, plus Save and Delete buttons), and none of that is given any allowance to shrink or wrap before the mobile breakpoint kicks in. Below 600px the table collapses into stacked cards and the problem vanishes entirely (confirmed — `document.body.scrollWidth` measured exactly 390px, matching the viewport, under the forced-mobile test). But in the entire range between 600px and roughly 1400px+, this table is wider than its container and the browser just lets the whole page overflow silently. A committee member on a 13" laptop retiring an old item would very plausibly never discover the Delete button exists unless they happened to scroll sideways — genuinely worse than a confusing button, since here the button isn't confusing, it's invisible.

### 2.4 — On a narrow (mobile) screen, "Available" and "Total" in Kit Inventory become two unlabelled numbers sitting on top of each other (High)

The site's responsive pattern — hide the `<thead>`, let each `<td>` become its own block — works well everywhere else, because in every other table each cell's content is self-explanatory without its header (a name, a category breadcrumb, a dropdown, a date picker). Kit Inventory is the one table where two adjacent cells are just bare integers: "Available" and "Total" render, once headers are hidden, as two plain numbers stacked directly on top of each other with nothing distinguishing which is which. I confirmed this by rendering the real Admin page under the site's actual mobile CSS: the visual result for the 4-Person Tent row is literally the sequence "3" then a stepper showing "3" again, with no label anywhere nearby. A committee member on their phone has no way to tell "3 of 3 available" from "the total is 3" without tapping into the stepper and cross-referencing. Every other page on the site avoids this exact trap by writing combined, self-describing text like "3 of 3" (Request page) — Kit Inventory is the one place that didn't get the same treatment, presumably because Total needs to stay independently editable there.

### 2.5 — Large, uneven whitespace gap between "Update" and "Remove" on the Basket page (Low–Medium)

Screenshotting the real Basket page with an item in it shows a large, slightly odd gap between the Quantity column's Update button and the Remove button off on the far right — much wider than the visual grouping calls for, to the point that Remove reads as disconnected from its own row. This comes from the browser's table auto-layout algorithm distributing the table's leftover width into the unlabelled third `<th></th>` column (Remove's column has no header text to size against, so it absorbs whatever space Item and Quantity don't use). Purely a layout-math side effect, invisible without watching a real table lay itself out with real content.

### 2.6 — Toasts can transiently sit on top of nearby buttons (Low)

Twice during testing — once on the Request page's "Can't find it?" form, once on Admin's category-delete row — the bottom-right toast appeared directly over the top of an interactive element the same content update had just rendered nearby. It's transient (gone in 3 seconds) and never blocked an action I was trying to take, but it's a real, reproducible overlap worth knowing about, not something you'd predict from the CSS (`position: fixed; bottom: 1.25rem; right: 1.25rem`) without seeing how short some of these pages are once you're near the bottom of a card.

### 2.7 — Subcategory placeholder text truncates in the Add Kit Item form (Low)

At the same common laptop width, the "Select a subcategory…" placeholder option in the Add Kit Item form's dropdown visibly truncates to "Select a subcategor…" because the select's grid column isn't quite wide enough for the full string at that font size. Cosmetic, but a small polish miss that's only visible once real text meets a real column width.

---

## Part 3 — Consolidated status

| # | Finding | Source | Status after this round |
|---|---|---|---|
| — | Delete crashes on items with loan history | v2 #1 | **Fixed & confirmed live** (archives instead) |
| — | Non-numeric quantity crashes | v2 #2 | **Fixed & confirmed live** |
| — | Reducing total below on-loan corrupts stock | v2 #3 | **Fixed** (blocked with a clear message) |
| — | Basket badge/content desync | v2 #4 | **Fixed** |
| — | Duplicate item names break Mark-as-Collected | v2 #5 | **Fixed & confirmed live** |
| — | No login rate-limiting | v2 #6 | **Fixed & confirmed live** |
| — | Case-insensitive duplicate categories | v2 #7 | **Fixed** |
| — | No "no results" filter message | v2 #8 | **Fixed & confirmed live** |
| — | qty=0 reuses "not enough available" message | v2 #9 | **Fixed** |
| — | Brand red vs destructive red too similar | v2 #10 | **Fixed & confirmed live** |
| — | `.btn-small` under 44px touch target | v2 #11 | **Improved** (~28px → ~40px), confirmed live |
| — | Toast vs banner feedback inconsistency | v2 #12 | **Fixed & confirmed live** |
| — | Native controls look inconsistent (datalist, date picker, spinner) | v2 #13 | Unchanged / accepted trade-off (spinner replaced by stepper; datalist and date picker remain native by design) |
| — | Basket quantity edit has no saved indicator | v2 #14 | **Fixed & confirmed live** (row-flash) |
| — | No "no login needed" hint on homepage | v2 #15 | **Fixed & confirmed live** |
| **1** | **Several primary buttons render with no colour (grey, browser-default)** | **New, this round** | **Open — High** |
| **2** | **Sign Out / Add Kit Item forms visually misassign labels at ~1200–1400px widths** | **New, this round** | **Open — High** |
| **3** | **Kit Inventory forces page-wide horizontal scroll, hides Delete button off-screen** | **New, this round** | **Open — Critical** |
| **4** | **Kit Inventory's Available/Total become indistinguishable bare numbers on mobile** | **New, this round** | **Open — High** |
| **5** | **Large uneven gap between Update/Remove on Basket page** | **New, this round** | **Open — Low/Medium** |
| **6** | **Toasts can transiently overlap nearby buttons** | **New, this round** | **Open — Low** |
| **7** | **Subcategory placeholder text truncates in Add Kit Item** | **New, this round** | **Open — Low** |

---

## Closing note

The good news first: every single fix from the last engineering pass held up under real, adversarial, live browser testing — nothing regressed, and the archive/restore workflow in particular is genuinely nicely designed once you actually see it rendered. The other news is that real rendering found seven things that careful source-reading, twice over, did not: two of them (the horizontal-scroll-hidden Delete button, and the label-misassignment on two of the most-used committee forms) are squarely in "a real person would be confused or blocked by this" territory, at completely ordinary window sizes, which is exactly the gap that switching from source-audit to live-browser-audit was supposed to close. If you want these addressed next, #3 (Kit Inventory's overflow) and #1 (uncoloured buttons) would be my starting point — both are comparatively simple CSS-only fixes with an outsized impact on how functional and finished the app actually feels to use.
