# CUMC Kit Store Tracker — Exhaustive Usability & UI/UX Audit (Round 2)

**Date:** 4 July 2026
**Scope:** Multiple use cases and edge cases per persona, plus a line-by-line visual/interaction audit (colour, spacing, button sizing, interaction patterns). No code was changed — this is a report only.

## Methodology

Two layers, same as before but deeper this time:

1. **Black-box functional testing.** Every scenario below was actually run against a live local instance — real HTTP requests, real form submissions, real database state checked before/after. Where I say something crashes or behaves a certain way, it's because I reproduced it, not because I inferred it from reading the code. Server tracebacks are quoted where relevant.
2. **Visual/interaction audit from source.** Still no working headless browser in this sandbox (same limitation as last time — Playwright's Chromium downloads fine but won't launch without system libraries I can't install here), so the visual judgements are computed from the actual shipped `style.css` and template markup: exact hex values, contrast ratios, padding/margin figures, effective button heights, and so on — not guesses. Anywhere I compare two numbers, I did the arithmetic.

I role-played each persona through their scenarios in first person so the "why does this feel off" reactions are grounded in a concrete mental model of who's using it and why, not generic checklist language. Each scenario still ends with what actually happened when tested.

---

# PART 1 — The Club Member

*I'm a second-year member. I've never seen this system before. I want gear for a trip and I do not want to think hard about it.*

## CM-1: First visit, just browsing

I land on the homepage. Big heading, one sentence of description, two buttons: "Request Kit" (solid red) and "Committee Login" (white with a red outline). That's a clean, honest homepage — I know immediately which button is mine. The two buttons are visually paired (same height, same border-radius, sitting side by side with a 0.75rem gap), which reads well: they look like a deliberate choice between two paths, not one primary action with an afterthought next to it.

One thing I notice immediately: nothing on this page tells me I don't need an account. A first-time member might hesitate at "Request Kit," unsure if it's about to ask them to sign up. A one-line "no login needed" under the button would remove that hesitation — small, but it's the very first thing anyone sees.

I click "Request Kit."

**Tested:** loads fine, 200 OK.

## CM-2: Browsing the catalogue

The page has a card containing a category dropdown, a subcategory dropdown, then a table of every item with an "Add to Basket" mini-form per row. Below that, a second card: "Can't find it?" with a free-text fallback.

Sitting with this for a minute:

- The category and subcategory dropdowns are **plain native `<select>` elements**, not a styled mega-menu or a sidebar of expandable categories (the kind of "sub-menu" pattern you'd see on a proper outdoor-gear retailer site, where hovering "Hardware" flies out a panel of "Single Rope / Static Rope / Karabiners / …"). Native selects are the right call here for a few reasons I can actually verify: they work with zero JavaScript as a fallback, they're keyboard-operable by default, and on a phone they hand off to the OS's own picker wheel, which is far more thumb-friendly than a custom flyout would be. The trade-off is speed for someone who already knows exactly what they want — a flyout submenu lets you go Hardware → Single Rope in one continuous mouse gesture, whereas here it's open dropdown → click → dropdown closes → open second dropdown → click. For a club kit list (dozens of items, not thousands), that's a perfectly reasonable trade to make. I wouldn't change this.
- Picking a category that happens to have **zero items in it** (I tested this — a category with no subcategories/items under it, which can genuinely happen once an admin starts organizing categories before adding stock) just leaves me looking at an empty table with headers and nothing underneath. There's no "no items match this filter" message. It looks like the page broke, not like the filter worked correctly. This is the single most confusing moment I hit as a browsing member.
  **Tested:** confirmed — the "no kit in the store" message only ever checks whether the *server* sent zero items at all; it has no idea the client-side filter just hid every row.
- The table itself: Item / Category / Available / Qty / (button). "Available" shows as `8/10` — clear, no ambiguity about what the two numbers mean once you look at the header, but on first glance `8/10` could be read as a fraction, a score, or a ratio before you connect it to the column header above. A small label like `8 of 10` or a tooltip wouldn't hurt, though this is genuinely minor.
- Each row's "Add to Basket" control is a number input (default value `1`) next to a small red button. The number input has the browser's native up/down spinner arrows — tiny (roughly 12–14px wide depending on the browser), fiddly to hit accurately with a mouse, and on Safari they don't render as visible arrows by default at all unless you hover. Compare this to how most shopping sites do quantity now: a `−` / `1` / `+` stepper built from three elements, each a real button-sized tap target. What's here works, but it's the "free" native option rather than the more usable one, and on a touchscreen those native spinner arrows are close to unusable — you'd just type over the `1` instead, which does work, but isn't obvious it's an option versus tapping arrows that are almost too small to tap.
- Hovering a row now highlights it in a very light grey (`--color-bg`, `#f6f7f5`) — a small polish touch that helps you track which row you're about to click "Add to Basket" on in a long list. Good detail, worth keeping.

**Tested:** added an item, quantity 1 → toast appeared bottom-right, nav badge updated to "(1)" without a page reload. Genuinely smooth.

## CM-3: Building a real trip basket (multi-item, multi-category)

This is the scenario I actually came here for: I need a rope, a tent, and a stove for a weekend.

I add the rope from Hardware, switch the category filter to Soft Goods, add a tent, switch again to Camping & Cooking, add a stove.

**Tested, three separate `Add to Basket` clicks with category switches in between:** all three landed in one basket correctly, no items lost or duplicated when switching filters mid-session — good, filtering the *view* never touches what's already in the basket, which is exactly the mental model I'd expect and is easy to get wrong if the two were accidentally coupled.

I go to the Basket page. Table: Item / Quantity (an editable number + "Update" button) / a "Remove" button. Below that, a second card: my name, email, and a notes box, then "Submit Request."

- The Quantity column mixes a text label ("Quantity") with an inline edit-and-confirm control — there's no visual indication that this field is "dirty" until I click Update. If I change `2` to `5` and get distracted, walk away, and come back, I have no way to tell whether I actually saved that change or not just by looking at the number in the box (it'll show `5` either way, saved or not). A "saved" checkmark flash, or just making the number update instantly on blur without needing an explicit button, would remove that ambiguity. As it is, forgetting to click "Update" is an easy, invisible mistake.
- Removing an item now removes just that row via `fetch()` and a toast, no reload — same smooth pattern as adding. When I remove the last item, though, the page **hard-reloads** rather than smoothly collapsing to the empty state. That's a deliberate, reasonable engineering shortcut (I can see why — swapping in the empty-state markup client-side would need duplicating the Jinja logic in JavaScript), but it does mean the very last removal in a session feels different from every removal before it: a flicker/full navigation where the others were instant.
- The checkout form's "Your name" field is required and the browser will genuinely stop me if I leave it blank (native HTML5 validation, no JavaScript needed) — that's a real inline validation moment I initially assumed didn't exist. Where it falls short: the **email field has no real validation at all beyond the browser's basic "looks like it has an @ in it" check**, and there's nothing stopping me from submitting complete garbage in it if I fake the request outside a browser.
  **Tested:** submitted `requester_email=not-an-email-at-all` directly — server stored it verbatim, no rejection, no flag.
- The **Notes textarea has no character limit, visible or enforced**, and no placeholder text suggesting what to put there beyond the label ("dates needed, trip details, etc.") — fine for now, but if someone pastes an essay, the box just scrolls; there's no resize handle disabled and no max-height, so a genuinely enormous paste would make the card balloon vertically with no cap.

**Tested the full submit:** name filled, three items in basket → 302 redirect → landed back on the browse page → basket badge disappeared → confirmed in the database all three lines were written with the same `request_group`, so they'll show up as one grouped card for committee, not three separate unrelated requests. That part works exactly as intended.

## CM-4: "Can't find it" — requesting something not in the catalogue

I need to borrow a bike lock, which isn't kit the club tracks in the system.

I type "Bike lock" into the free-text box, quantity 1, click Add to Basket.

- This form sits in its own card labelled "Can't find it?" — good, it's clearly a fallback rather than competing visually with the main catalogue table for attention.
- **There's no confirmation of what I typed once it's in the basket** beyond going to check the basket page — the toast says "Added 1 x Bike lock to your basket," which is fine, but the input field itself doesn't visibly clear or acknowledge success beyond that toast. I tested this: after a successful AJAX add, the form does reset (the text field goes blank) — so it *is* handled, I just want to flag that this reset-on-success behaviour is invisible if you're not watching closely, since there's no visual "cleared!" moment beyond the field emptying.

**Tested an edge case here on purpose:** submitted `<script>alert(1)</script>` as the item description. It came back on the basket page as literal escaped text (`&lt;script&gt;`), not executed — Jinja's auto-escaping is doing its job, no XSS. Good, and worth saying plainly: the free-text field is safe.

**Tested another edge case:** left the description blank and just clicked Add to Basket. Correctly rejected with "Choose an item or describe what you need" — sensible, matches the same validation for the catalogue's hidden `item_id` field.

## CM-5: Getting the quantity wrong

I fat-finger the quantity field and type `abc` instead of a number, then submit.

**Tested — this crashes the server.** A real HTTP 500 Internal Server Error, generic Flask error page, no useful message to me as a user:

```
ValueError: invalid literal for int() with base 10: 'abc'
File "app.py", line 117, in add_to_basket
    quantity = max(1, int(request.form.get("quantity") or 1))
```

In a real browser this specific path is hard to hit by accident, because the field is `<input type="number">`, which most browsers refuse to let you type letters into at all. But it's not impossible — some mobile keyboards, browser extensions, autofill tools, or just pasting text into the field can get non-numeric content into a number input, and the exact same crash happens if you do it on the **basket page's** quantity-update field too (I tested both). This is the only outright crash I found on the club member's side, and it's not a one-off: I found the identical unguarded pattern in four different places, two of which are on the committee side (more on that below). It's worth fixing everywhere at once rather than patching this one spot.

**Also tested:** quantity `999999999`. Accepted without complaint, no upper bound anywhere in the request flow. Since a request is just a wish-list item for committee to review (not an actual stock deduction), this is low-stakes, but a silly number sitting in the pending-requests list is a bit of a paper cut for whoever reviews it.

## CM-6: What happens if the thing in my basket disappears from under me?

Slightly obscure, but I actually managed to trigger this by accident, so I chased it down properly: I add Walking Poles to my basket, then — independently, in another tab logged in as committee — the item gets deleted from the catalogue entirely (a real thing that could happen if an admin decides to retire an item while I'm mid-session).

**Tested carefully, with the exact sequence:**
1. Added 2x Walking Poles → nav badge correctly shows "(2)".
2. Item deleted from the catalogue by committee.
3. Reloaded the browse page: **nav badge still says "(2)"** — it never re-checks whether the thing it's counting still exists.
4. Opened the basket page: correctly shows the empty-state message ("Your basket is empty") because the basket page *does* check for existence when building the display list.

So for a little while, the badge in the header is actively lying to me — it says I have 2 things in my basket, and the page one click away says I have none. That's a real inconsistency between two pieces of code that are supposed to describe the same state, caused by one of them (the badge count) trusting raw numbers stored in my session and the other (the basket page) validating them against the live catalogue. It's a narrow window (an item has to be deleted mid-session), but it's the kind of bug that's genuinely confusing rather than merely cosmetic when it does happen.

## CM-7: Mobile / narrow-screen check (from source, not a live screenshot)

I don't have a real phone screenshot to compare here (see the note in Methodology), but I can read exactly what the CSS does below 600px width, which is the app's only breakpoint:

- Every table collapses from a grid into stacked "cards" (each `<tr>` becomes a block, headers disappear). This is a well-known, solid responsive-table pattern — I'd compare it favourably to sites that just let a wide table scroll horizontally off-screen, which is worse on a phone.
- The category/subcategory filter dropdowns and the header nav stack vertically, which is correct and expected.
- What I can't verify without a real device: the small buttons (see the dedicated sizing section below) get *more* important to get right on mobile, not less, and this breakpoint doesn't do anything to grow them — they're the same `0.3rem 0.7rem` padding at 375px width as they are at 1400px width. On the exact layout meant for touch, touch targets don't get any bigger.

---

# PART 2 — The Committee Member

*I've got the shared password. I'm the one who actually knows where the head torches live. I do this a few times a week, so anything clunky here costs me real time, repeatedly.*

## CB-1: Logging in, correctly and incorrectly

The login page: one card, centred, a password field with `autofocus` (cursor's already in the box when the page loads — nice, saves a click), and a red "Log In" button.

**Tested wrong password three times in a row:** every single time, just "Incorrect password," page reloads, box empties. **There is no lockout, no rate limit, no delay after repeated failures.** For a shared password protecting an admin panel, that's worth knowing even for a low-stakes club tool — it means the password can be brute-forced as fast as a script can submit the form, with the login page giving zero resistance. I'm not saying this needs enterprise-grade auth, but "no delay whatsoever after N failures" is a specific, checkable fact about the current behaviour.

**Tested logging in when I'd been sent here by a protected page** (I typed `/admin` in directly while logged out): correctly bounced to `/login?next=/admin`, and after a correct password, correctly landed back on `/admin` instead of the generic sign-in-out default. That's a genuinely good detail — it respects where I was trying to go, not just where the app assumes I want to go.

## CB-2: The day-to-day sign-out/sign-in loop

This is what I do most: someone wants a helmet, I find it, I hand it over, I log it.

The Sign Out form: category filter, subcategory filter, item picker, borrower name (now with autocomplete, more on that in a second), quantity, notes, then a red "Sign Out" button.

**Tested the borrower autocomplete specifically, since it's new:** typed "Jo" and got a native browser suggestion list showing previously-used names. This is a `<datalist>`, which means:
- It's genuinely accessible and keyboard-operable, which a hand-rolled JS autocomplete very often isn't without real effort.
- But it inherits every visual inconsistency of the browser's own dropdown rendering — it does **not** look like the rest of the app. It's plain black-on-white system font in Chrome, styled completely differently in Firefox, and I can't add anything to it (no "last borrowed this on 12 June" hint, no distinguishing two people with the same first name). It's the one control in the whole app that looks like it belongs to the operating system rather than to this site. Functionally fine, visually the odd one out.
- It also only matches **from the start of what I type** in some browsers and anywhere-in-the-string in others — that's not a bug in this app, it's just an inherent quirk of `<datalist>` filtering being browser-dependent, worth knowing rather than assuming it behaves identically everywhere.

**Tested signing out more than available:** correctly blocked, clear message ("Only 8 of 'Climbing Helmet' available").

**Tested signing out exactly zero:** also blocked — but the message is the *same* "Only 8 of 'Climbing Helmet' available" message used for asking for too many. That's confusing on reflection: I asked for **zero**, and the app's answer to "why not" is "because there's only 8," which doesn't actually explain the real problem (you can't sign out nothing). Two different failure reasons are sharing one message.

**Tested signing back in, plus a double-click scenario** (resubmitting the sign-in on an already-returned loan by replaying the exact same request): handled gracefully, no error, no double-counting of stock — good defensive coding, even though this exact scenario is hard to trigger by accident once the button legitimately disappears from the list after the first click.

## CB-3: Reviewing a member's basket request

A member submitted a 2-item request. On the Admin page, it shows as one card: their name, email, timestamp, their note in quotes, then a small table of the two items with individual Approve/Deny per row, plus "Approve All / Deny All" at the top of the card since there's more than one item.

- This grouping is the single best piece of information architecture in the app, genuinely — it mirrors how a form-builder tool like Google Forms groups a respondent's multiple answers into one card rather than flattening everything into one giant table. It's the right mental model: "here's what Sam wants," not "here are six unrelated rows that happen to share a name."
- **Tested a single-item request specifically** (submitted via the same basket flow, but only ever containing one item): confirmed the "Approve All / Deny All" buttons correctly do **not** appear when there's only one line — so the UI doesn't show a redundant "do this to all one of them" control. That's a small but correct restraint.
- The quoted note ("their note in quotes") uses smart-quote HTML entities (`&ldquo;` / `&rdquo;`) — a nice typographic detail that most quick internal tools wouldn't bother with.
- Once resolved, the request drops into "Recently Resolved Requests" (capped at the 20 most recent) with a coloured status badge: green-tinted "approved," pink-tinted "denied," and — the new one — a pale blue "collected" once it's been converted into an actual loan.

**Tested "Mark as Collected" end-to-end:** approved a request for 2x Trangia Stove, clicked "Mark as Collected," and confirmed directly in the database that a real `SignOut` row was created, stock dropped from 6 to 4, and the request is now flagged as fulfilled so the button turns into a static "Collected" badge and can't be clicked twice. This closes the gap flagged as the top finding in my last report, and it holds up under testing.

**Tested three ways this can go wrong, deliberately:**

1. **A request for something not in the catalogue** (a member typed a custom item like "Personal Bivvy" that has no matching `KitItem`). Correctly shows "No matching item — sign out manually" instead of a button that would do nothing or crash. Good, honest fallback.
2. **A request for more than is currently available** (member asked for 99 tents, only 3 exist). Approving it works (approval doesn't check stock — it's just a "yes" from committee), but clicking "Mark as Collected" afterwards correctly refuses: "Only 3 of '4-Person Tent' available right now — adjust the loan or sign it out manually." Good, it catches the problem at the point where it actually matters (handing over real stock), not earlier.
3. **Two catalogue items sharing the exact same name.** This one's a genuine, non-obvious problem I found by testing whether the system stops you from creating a duplicate name — it doesn't. I added a second "Climbing Helmet" entry (different subcategory, different quantity) alongside the existing one. **"Mark as Collected" matches purely by item name text**, so with two items called "Climbing Helmet" in the system, approving a request for one could silently decrement stock from the *other* one instead, depending purely on database row order — invisible to whoever clicks the button, and only discoverable later when someone notices the wrong helmet's count is off. This is a real, if narrow, data-integrity risk that stems from there being no uniqueness constraint on item names.

## CB-4: Managing categories, subcategories, and stock

**Tested creating a category, then a subcategory under a wrong/missing category selection:** correctly blocked ("Subcategory name and category are required").

**Tested a genuinely sneaky case: the same subcategory name under two different categories** (e.g. "Helmets" already exists under Hardware; I add another "Helmets" under Camping & Cooking). **This succeeds** — and arguably it should, since the uniqueness rule is deliberately scoped per-category. But it does mean the Subcategories list in the admin panel now shows two entries labelled "Helmets," distinguished only by a small `(Category Name)` suffix in grey next to each — easy to misread at a glance, especially since the list has no visual grouping or indentation by parent category, just a flat list with a parenthetical.

**Tested category name case-sensitivity:** created "Hardware" (already exists) and then "hardware" (lowercase). **Both exist side by side** — the duplicate check is case-sensitive, so a moment's inattention creates two categories that look like a data-entry mistake to anyone who looks at the list afterwards, and there's no merge/rename tool to fix it (you can only add and delete, not edit a name in place — that's from my last report and still true).

**Tested reducing an item's total quantity below how many are currently signed out — this is the most serious finding in this whole pass.** I signed out 8 of 10 helmets (2 left available), then, as committee, edited that same item's total quantity down to 2 (say I made a stocktake correction and thought only 2 existed all along). The edit succeeded silently. Result:

```
before: total=10, available=2  (8 out on loan)
after:  total=2,  available=0  (still 8 out on loan in reality)
```

The system now believes there are 2 helmets total and 0 available — but there are physically 8 sitting with borrowers who haven't returned them yet, plus 2 on the shelf. If even one of those 8 gets signed back in later, the sign-in logic does `available += quantity`, which would push `available` **above** `total` (e.g. 0 + 1 = 1 available out of a total of 2 — fine so far, but after all 8 trickle back in, available would reach 8 against a total of 2, a number that's simply impossible and would show as "8/2 available" on every page that renders it). This isn't a crash, which is exactly what makes it worse than one — it's a silent, compounding data-integrity problem that only becomes visible days or weeks later when the numbers stop making sense, by which point nobody will remember why.

**Tested deleting a kit item that has ever been signed out, even if fully returned — this crashes.** The delete guard only checks for *currently active* loans (`any(so.is_out for so in item.sign_outs)`), so I deleted a helmet with zero active loans but one historical, fully-returned loan on its record, and got a full server crash:

```
sqlalchemy.exc.IntegrityError: NOT NULL constraint failed: sign_outs.item_id
[SQL: UPDATE sign_outs SET item_id=? WHERE sign_outs.id = ?]
```

In plain terms: the database won't let a `SignOut` row point at a deleted item, and nothing in the code told the database what to do about that beforehand, so it just fails loudly. Practically speaking, this means **any item that has ever been loaned out, even once, can never be deleted again for the rest of the system's life** — which for a club that's been running this for a term or two, is most of the inventory. This is the highest-severity issue in this entire report: it's a guaranteed crash on an action (retiring old kit) that a committee will definitely want to do eventually.

**Tested the "Add Kit Item" form with no subcategory chosen:** correctly blocked both by the browser (the `<select required>` won't let native submission through empty) and by the server as a backstop. Good defence in depth — the same double-check pattern (browser + server) shows up consistently across every required field in this app, which I'll say plainly is good practice, not every hobby project bothers with the server-side half once the client-side check exists.

## CB-5: Logging out

**Tested:** logout clears the session, redirects home, and immediately re-tests as blocked when I try to hit `/admin` again directly by URL. Clean, no lingering access. No surprises here.

---

# PART 3 — Systematic Design Audit (colour, shape, size, pattern-by-pattern)

This is the "don't gloss over small details" section, organised by design property rather than by persona, since several of these apply everywhere at once.

## Colour

Current palette (from `style.css`, computed contrast ratios via the standard WCAG relative-luminance formula):

| Pairing | Ratio | Verdict |
|---|---|---|
| White text on primary red button `#bc1016` | ~6.5:1 | Passes AA comfortably |
| Header nav text `#eef3ef` on `#bc1016` | ~6.0:1 | Passes AA (this was the failing pairing before the red was darkened last round — now fixed) |
| Success text `#256b40` on light green flash/badge `#e4f4e9` | ~5.7:1 | Passes AA |
| Error text `#b3261e` on light pink flash/badge `#fbe9e7` | ~5.6:1 | Passes AA |
| Muted grey label text `#5d6a63` on white card | ~5.7:1 | Passes AA |

All the accessibility-contrast issues from the previous report are genuinely resolved now — I re-measured rather than assumed.

**A finding contrast math doesn't catch, but a person will:** `--color-primary` (`#bc1016`) and `--color-error` (`#b3261e`) are both dark brick-reds, distinguishable side by side but easy to confuse in a quick glance or under less-than-ideal lighting/screen conditions — R:188 vs 179, G:16 vs 38, B:22 vs 30, i.e. genuinely close. Every primary action in the app (Log In, Sign Out, Submit Request, Add Item — the *good*, forward-progress actions) is rendered in almost the same red as every destructive action (Delete, Deny). Red-as-brand-colour is a legitimate choice (club colours, presumably), but it puts the app in tension with the near-universal convention that red means "stop / danger / undo this." A colourblind user (red-green deficiency affects roughly 1 in 12 men) would have an even harder time telling "this button moves me forward" from "this button destroys something" than someone with typical colour vision, since both buttons already sit close together in hue *and* a common form of colourblindness compresses exactly that part of the spectrum further. I'm not suggesting the club colour needs to change — just flagging that the *danger* colour sitting this close to the *brand* colour is worth a second look, e.g. shifting `--color-error` toward orange or keeping red but adding a distinct icon/weight difference on destructive buttons.

## Button shape, size, and consistency

- Border-radius scale: buttons and form fields both use `6px`; cards, the request-group box, and toasts use `8px` (`--radius`); badges use `999px` (a full pill). That's a coherent three-tier system (controls / containers / status-pills) even though it's never written down anywhere as a rule — it just happens to be consistent, which I'll call out as a genuine strength rather than an accident to fix.
- Effective button height, computed from padding + line-height (not guessed):
  - Regular `.btn` (Log In, Add Item, Submit Request, Sign Out): ~0.95rem text at 1.5 line-height ≈ 22.8px, plus `0.55rem` padding top and bottom (≈8.8px each) ≈ **37px tall**.
  - `.btn-small` (Approve, Deny, Save, Delete, Sign In, Update, Remove — i.e. almost every button inside every table in the whole app): ~0.85rem text at 1.5 line-height ≈ 20.4px, plus `0.3rem` padding top and bottom (≈4.8px each) ≈ **28px tall**.
- Apple's Human Interface Guidelines and Google's Material Design both recommend a **minimum 44–48px** tap target on touch devices; WCAG 2.5.5 (AAA, not required but a good bar) also lands at 44px, while the AA-level minimum is a much lower 24px. The regular buttons (~37px) sit in the reasonable middle ground — below the ideal, above the legal minimum. The small buttons (~28px) are the ones I'd actually worry about, because **they're concentrated exactly where committee does rapid, repeated actions** — approving a list of five request lines, or working down the inventory table — and they're also the buttons living inside the mobile-collapsed table rows, i.e. present on the exact form factor where finger-sized targets matter most.
- Every button uses the same `border: none` and relies entirely on background colour for definition — no button anywhere has a visible border/outline in its resting state (only on keyboard focus, which is new and good). This is a fine, modern, minimal look; I mention it only because it means colour is doing 100% of the work distinguishing button types (success/danger/primary/plain), which loops back to the red-on-red concern above — there's no secondary cue like a border or icon backing up the colour if the colour itself is momentarily hard to tell apart.

## Forms and inputs

- Label placement is consistent everywhere: bold-ish (`font-weight: 600`), small (`0.85rem`), muted grey, sitting directly above its input with `0.75rem` margin above and `0.25rem` below — a clean, repeatable rhythm I didn't find a single exception to across all seven templates.
- Every text input, select, and textarea shares identical padding (`0.5rem 0.65rem`), border colour, and border-radius — genuinely uniform, no rogue input anywhere rendering at a different height or style than its neighbours.
- The one outlier: the basket's quantity-update field and a couple of admin inline table inputs get an inline `style="width:4rem"` (or similar) written directly in the template rather than through a CSS class. It looks fine, but it means resizing that pattern later means hunting through templates for inline styles rather than changing one rule in `style.css`.
- `<input type="date">` for Purchase Date hands off entirely to the browser's native date picker. On Chrome/Edge this is a small calendar icon and popup calendar; on Safari it's a scrolling wheel; on Firefox it's yet another look again. None of these match the app's own visual design at all — same category of issue as the borrower-name datalist, and same trade-off: free accessibility and mobile support, zero visual control.

## Interaction-pattern comparisons (as requested, one by one)

| Pattern used here | Alternative pattern | Trade-off |
|---|---|---|
| Cascading category → subcategory `<select>` dropdowns | A flyout/mega-menu sub-menu (hover "Hardware," a panel of subcategories appears) | Dropdowns: accessible, mobile-native, more clicks. Submenus: faster for power users on desktop, poor on touch, needs custom keyboard handling to be accessible at all. Current choice is the safer, more inclusive one. |
| Borrower name via `<datalist>` | A custom JS-rendered typeahead/combobox | Datalist: free, accessible, but unstyled and inconsistent across browsers, can't show extra info per suggestion. Custom combobox: fully on-brand, can show richer suggestions, costs real implementation and accessibility effort to get right. |
| Destructive actions confirmed via the browser's native `confirm()` popup | An in-page modal styled to match the app | `confirm()` is the most jarring visual break in the entire app — it's the one moment where the interface suddenly looks like a completely different, unstyled program (literally shows "says" text and the page's own address, depending on browser), blocks the whole page while open, and can't be restyled with CSS at all, ever. An in-page modal would look and feel like part of the same product, at the cost of needing to build (and make accessible) a modal from scratch. |
| Basket add/update/remove now use `fetch()` + a floating toast | The rest of the app (login, sign-out, admin CRUD, request resolution) still uses full-page reload + a top-of-page banner | These are two genuinely different feedback systems living side by side. A club member doing basket actions sees floating bottom-right toasts; a committee member doing everything else sees banners at the very top of the page that require scrolling up to notice if they've scrolled down a long table first. Neither pattern is wrong on its own, but having both in one small app is an inconsistency a user will eventually notice, even if they can't articulate why. |
| Quantity entry via native number-input spinner arrows | A three-part `−` / value / `+` stepper | Native spinners are effectively too small to use accurately with a mouse and inconsistent across browsers (Safari hides them by default); a custom stepper is a well-worn, larger-target pattern most shopping carts use for exactly this reason. |

---

# Consolidated Findings

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | Deleting any kit item that has **ever** been signed out (even fully returned) crashes with a database integrity error | **Critical** | Admin → Kit Inventory → Delete |
| 2 | Non-numeric quantity input crashes the server (HTTP 500) with no user-facing message | **High** | `/basket/add`, `/basket/update`, and the same unguarded pattern exists in the sign-out and admin-item quantity fields |
| 3 | Reducing an item's total quantity below what's currently on loan silently corrupts the available/total relationship (can later show "available" exceeding "total") | **High** | Admin → Kit Inventory → Save |
| 4 | Nav basket-count badge and actual basket contents can drift out of sync if an item is deleted while sitting in someone's basket | Medium | Header badge vs. `/basket` |
| 5 | "Mark as Collected" matches items by name only; two catalogue items sharing a name can silently decrement the wrong one's stock | Medium | Admin → Recently Resolved |
| 6 | No login rate-limiting or delay on repeated wrong-password attempts | Medium | `/login` |
| 7 | Category/subcategory names aren't checked for near-duplicates (case-insensitive), producing confusing look-alike entries | Low–Medium | Admin → Manage Categories |
| 8 | Client-side category/subcategory filters can produce an empty table with no "no results" message | Low–Medium | `/request`, `/sign-in-out` |
| 9 | Same error message used for "quantity too high" and "quantity is zero" on sign-out | Low | Sign In/Out |
| 10 | Brand red and destructive-action red are close enough in hue to be confused at a glance, especially for colourblind users | Low–Medium | Global colour palette |
| 11 | `.btn-small` (used in almost every table row across the app) has an effective touch target of ~28px, well under the 44px mobile guideline | Low–Medium | Global |
| 12 | Two different feedback systems (toast vs. top-of-page banner) depending on whether the action used JavaScript | Low | Global |
| 13 | Native browser controls (datalist, date picker, `confirm()`, number spinner) each look visually inconsistent with the app's own styling and with each other | Low | Sign-out form, Add Item form, all delete buttons |
| 14 | Basket quantity edits require an explicit "Update" click with no saved/unsaved indicator | Low | `/basket` |
| 15 | No indication anywhere that the Request flow requires no login | Low | Homepage |

---

## Closing note

Nothing here needed a screenshot to find — every crash was reproduced with a real request, every contrast ratio was computed from the real hex values, every button height was computed from the real padding and font-size. The two items I'd genuinely act on first are #1 and #2 (both are outright crashes reachable through very ordinary, foreseeable committee behaviour — retiring old kit, and quantity fields being fallible), followed by #3, since it corrupts data quietly rather than announcing itself. Everything from the colour/shape/pattern section is real and worth knowing, but it's polish sitting on top of a foundation that, aside from those three, held up well under genuinely adversarial testing.
