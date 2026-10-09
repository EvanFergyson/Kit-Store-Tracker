document.addEventListener("DOMContentLoaded", () => {
  promoteFlashesToToasts();
  initAjaxBasketAdd();
  initAjaxBasketMutations();
  initQtySteppers();
  initSessionOtherToggle();
  initGroupSplitTotals();
  scrollActiveSubnavIntoView();
  initSerialDecode();
  initRetireToggle();
});

// The app has two feedback systems: floating toasts (basket add/update/
// remove, which use fetch() and never reload the page) and top-of-page flash
// banners (everything else, which use a normal form POST + redirect). Rather
// than leave both patterns visible side by side, promote every server-
// rendered flash message into a toast as soon as JS runs, so anyone with
// JavaScript enabled always gets feedback in the same place. Browsers with
// JS disabled still see the flash banner (it's plain server-rendered HTML
// and was never depending on this script), so there's no regression there.
function promoteFlashesToToasts() {
  const stack = document.querySelector(".flash-stack");
  if (!stack) return;
  stack.querySelectorAll(".flash").forEach((el) => {
    showToast(el.textContent.trim(), el.classList.contains("flash-error"));
  });
  stack.remove();
}

// Small floating confirmation that appears near where the user is looking
// instead of the page-top flash banner, so adding something to the basket
// doesn't require scrolling up to see it worked.
function showToast(message, isError) {
  const container = document.getElementById("toast-container");
  if (!container || !message) return;

  const toast = document.createElement("div");
  toast.className = "toast" + (isError ? " toast-error" : " toast-success");
  toast.textContent = message;
  container.appendChild(toast);

  // Add the "visible" class on the next frame so the CSS transition runs.
  requestAnimationFrame(() => toast.classList.add("toast-visible"));

  setTimeout(() => {
    toast.classList.remove("toast-visible");
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

function updateBasketBadge(count) {
  // The count appears in the rail, the phone tab bar and the top-bar link.
  document.querySelectorAll(".js-basket-count").forEach((badge) => {
    badge.textContent = count;
    badge.hidden = count <= 0;
  });
  document.querySelectorAll(".js-basket-count-text").forEach((text) => {
    text.textContent = ` (${count})`;
    text.hidden = count <= 0;
  });
}

// Forms with class "js-basket-add" (both the catalog "Add to Basket" rows
// and the "Can't find it?" custom form) submit over fetch instead of a full
// page navigation, so browsing a long list doesn't lose your scroll position
// every time you add something. If the fetch fails for any reason (JS error,
// offline, etc.) the form falls back to a normal submit so the action still
// completes - it just costs a page reload like before.
function initAjaxBasketAdd() {
  document.querySelectorAll("form.js-basket-add").forEach((form) => {
    form.addEventListener("submit", function handleSubmit(event) {
      if (form.dataset.bypass) return; // let the fallback submit go through untouched
      event.preventDefault();

      fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: new FormData(form),
      })
        .then((response) => response.json())
        .then((data) => {
          showToast(data.message, !data.ok);
          updateBasketBadge(data.basket_count);
          if (data.ok) form.reset();
        })
        .catch(() => {
          form.dataset.bypass = "true";
          form.submit();
        });
    });
  });
}

// Same fallback-safe pattern for adjusting/removing basket lines on the
// basket page itself, so editing quantities doesn't reload the whole page.
function initAjaxBasketMutations() {
  document.querySelectorAll("form.js-basket-update").forEach((form) => {
    form.addEventListener("submit", function handleSubmit(event) {
      if (form.dataset.bypass) return;
      event.preventDefault();

      fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        body: new FormData(form),
      })
        .then((response) => response.json())
        .then((data) => {
          showToast(data.message, !data.ok);
          updateBasketBadge(data.basket_count);
          // The toast confirms the save, but it's off in the corner - also
          // flash the row itself so the confirmation is visible right where
          // you're already looking, without needing an explicit "saved"
          // indicator sitting in the row permanently.
          if (data.ok) {
            const row = form.closest("tr.basket-row");
            if (row) {
              row.classList.remove("row-flash-saved");
              void row.offsetWidth; // restart the animation if clicked twice quickly
              row.classList.add("row-flash-saved");
            }
          }
        })
        .catch(() => {
          form.dataset.bypass = "true";
          form.submit();
        });
    });
  });

  document.querySelectorAll("form.js-basket-remove").forEach((form) => {
    form.addEventListener("submit", function handleSubmit(event) {
      if (form.dataset.bypass) return;
      event.preventDefault();

      fetch(form.action, {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((response) => response.json())
        .then((data) => {
          showToast(data.message, !data.ok);
          updateBasketBadge(data.basket_count);
          if (data.basket_count === 0) {
            // Simplest reliable way to swap in the "basket is empty" state
            // and hide the checkout form, rather than re-implementing that
            // markup in JavaScript.
            window.location.reload();
            return;
          }
          const row = form.closest("tr.basket-row");
          if (row) row.remove();
        })
        .catch(() => {
          form.dataset.bypass = "true";
          form.submit();
        });
    });
  });
}

// Wires up every ".qty-stepper" widget (a number input flanked by - / +
// buttons) so the buttons adjust the input's value directly. Kept as plain
// buttons with no "name" attribute, so they never interfere with the
// surrounding form's submission - only the input itself is submitted, same
// as a bare <input type="number"> always was.
function initQtySteppers() {
  document.querySelectorAll(".qty-stepper").forEach((stepper) => {
    const input = stepper.querySelector("input[type=number]");
    const down = stepper.querySelector(".qty-step-down");
    const up = stepper.querySelector(".qty-step-up");
    if (!input || !down || !up) return;

    // `|| 1` used to stand in for both "min wasn't set" and "min is 0" -
    // indistinguishable in JS since 0 is falsy, so a min="0" stepper (the
    // group-split rows on Sign In/Out, where 0 is a real, expected value
    // meaning "skip this item") had its floor silently forced up to 1.
    // Number.isNaN(...) only catches an actually-missing/invalid min.
    const parsedMin = parseInt(input.min, 10);
    const min = Number.isNaN(parsedMin) ? 1 : parsedMin;

    // Same issue reading the *current* value: `parseInt(input.value) || 1`
    // treated an explicit "0" the same as "nothing typed yet", so clicking
    // up from 0 jumped straight to 2. Only fall back (to min, not a
    // hardcoded 1) when the value truly isn't a number.
    const currentValue = () => {
      const parsed = parseInt(input.value, 10);
      return Number.isNaN(parsed) ? min : parsed;
    };
    const clamp = (value) => (Number.isNaN(value) ? min : Math.max(min, value));

    down.addEventListener("click", () => {
      input.value = clamp(currentValue() - 1);
    });
    up.addEventListener("click", () => {
      input.value = clamp(currentValue() + 1);
    });
    input.addEventListener("blur", () => {
      input.value = clamp(parseInt(input.value, 10));
    });
  });
}

// Live running total for a group-split form (splitting a group request/
// sign-out across several of its items - see .sign-out-group-form in
// sign_in_out.html and admin_resolved.html, which share this exact markup).
// Sums every quantity_<id> field as the committee member types, so they can
// see whether their split is right before submitting instead of finding out
// only after a round trip. Two shapes, depending on whether the form knows
// what its split needs to add up to:
//   - data-target-quantity set (collect_request's exact-sum case): shows
//     "N of target" and colors it to flag match vs. mismatch.
//   - not set (sign_out_group's freeform case, any nonzero total is fine):
//     shows a plain "Total: N" with no color judgement.
function initGroupSplitTotals() {
  document.querySelectorAll(".sign-out-group-form").forEach((form) => {
    const inputs = form.querySelectorAll('input[type="number"][name^="quantity_"]');
    const totalEl = form.querySelector(".sign-out-group-total");
    if (!inputs.length || !totalEl) return;

    const target = form.dataset.targetQuantity ? parseInt(form.dataset.targetQuantity, 10) : null;

    const update = () => {
      const sum = Array.from(inputs).reduce((total, el) => total + (parseInt(el.value, 10) || 0), 0);
      totalEl.classList.remove("total-match", "total-mismatch");
      if (target === null) {
        totalEl.textContent = `Total: ${sum}`;
      } else {
        totalEl.textContent = `${sum} of ${target}`;
        totalEl.classList.add(sum === target ? "total-match" : "total-mismatch");
      }
    };

    inputs.forEach((el) => el.addEventListener("input", update));
    update();
  });
}

// Wires up a Category -> Subcategory -> Item chain of <select> elements.
// Each <option> in the subcategory select is expected to carry
// data-category-id, and each <option> in the item select data-subcategory-id.
// Selecting a category hides non-matching subcategories (and vice versa for
// items); it never removes options, just hides ones that don't match so the
// underlying <select> still works normally without JS.
function initCascade(categorySelectId, subcategorySelectId, itemSelectId) {
  const categorySelect = document.getElementById(categorySelectId);
  const subcategorySelect = document.getElementById(subcategorySelectId);
  const itemSelect = document.getElementById(itemSelectId);
  if (!categorySelect || !subcategorySelect || !itemSelect) return;

  const subcategoryOptions = Array.from(subcategorySelect.options);
  const itemOptions = Array.from(itemSelect.options);

  function applySubcategoryFilter() {
    const subcategoryId = subcategorySelect.value;
    itemOptions.forEach((option) => {
      if (!option.value) return; // keep the placeholder option visible
      option.hidden = Boolean(subcategoryId) && option.dataset.subcategoryId !== subcategoryId;
    });
    itemSelect.value = "";
    itemSelect.dispatchEvent(new Event("change"));
  }

  function applyCategoryFilter() {
    const categoryId = categorySelect.value;
    subcategoryOptions.forEach((option) => {
      if (!option.value) return;
      option.hidden = Boolean(categoryId) && option.dataset.categoryId !== categoryId;
    });
    subcategorySelect.value = "";
    applySubcategoryFilter();
  }

  categorySelect.addEventListener("change", applyCategoryFilter);
  subcategorySelect.addEventListener("change", applySubcategoryFilter);
}

// Filters a list of elements (identified by class name), as a row of
// clickable "chip" buttons instead of a <select> dropdown - used on pages
// where the category is already fixed by the page itself (e.g. a
// per-category request or inventory page) and only subcategory needs
// filtering. One tap picks a subcategory directly, rather than opening a
// dropdown to find it.
function initChipFilter(chipsContainerId, rowClassName, noMatchMessageId) {
  const container = document.getElementById(chipsContainerId);
  if (!container) return;

  const chips = Array.from(container.querySelectorAll(".filter-chip"));
  const rows = Array.from(document.getElementsByClassName(rowClassName));
  const noMatchMessage = noMatchMessageId ? document.getElementById(noMatchMessageId) : null;

  function applyFilter(subcategoryId) {
    let visibleCount = 0;
    rows.forEach((row) => {
      const visible = !subcategoryId || row.dataset.subcategoryId === subcategoryId;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    if (noMatchMessage) noMatchMessage.hidden = visibleCount !== 0;
  }

  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      applyFilter(chip.dataset.subcategoryId);
    });
  });
}

// The checkout form's pickup/return session pickers each have their own
// "Other" text box, hidden until the member actually picks "Other" - and
// only required at that point, so the browser's built-in validation doesn't
// block submission over a box that isn't even relevant to the option they
// chose. Runs globally (guarded on finding no [data-session-group] elements)
// rather than only on the basket page, matching every other init* function.
function initSessionOtherToggle() {
  document.querySelectorAll("[data-session-group]").forEach((group) => {
    const name = group.dataset.sessionGroup;
    const otherInput = document.querySelector(`[data-session-other="${name}"]`);
    if (!otherInput) return;

    group.querySelectorAll('input[type="radio"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        const isOther = radio.value === "Other" && radio.checked;
        otherInput.style.display = isOther ? "" : "none";
        otherInput.required = isOther;
      });
    });
  });
}

// On phones the admin tabs are a single sideways-scrolling row, so a tab near
// the end (e.g. Resolved Requests) would start off-screen when it's active.
function scrollActiveSubnavIntoView() {
  const active = document.querySelector(".admin-subnav-link.active");
  if (!active) return;
  const nav = active.parentElement;
  if (nav.scrollWidth <= nav.clientWidth) return;
  nav.scrollLeft = active.offsetLeft - nav.offsetLeft - 16;
}

// Gear register: when brand + serial decode to a manufacture date, pre-fill
// the date field (never overwriting one already typed) and say where it came
// from, so the Gear Secretary checks it against the label.
function initSerialDecode() {
  document.querySelectorAll(".js-serial-decode").forEach((form) => {
    const brand = form.querySelector('[name="brand"]');
    const serial = form.querySelector('[name="serial"]');
    const made = form.querySelector('[name="manufacture_date"]');
    const hint = form.querySelector(".js-decode-hint");
    if (!brand || !serial || !made) return;
    let filledByDecode = false;

    const decode = async () => {
      if (made.value && !filledByDecode) return;
      const params = new URLSearchParams({ brand: brand.value, serial: serial.value });
      try {
        const response = await fetch(`${form.dataset.decodeUrl}?${params}`);
        const result = await response.json();
        if (result.date) {
          made.value = result.date;
          filledByDecode = true;
          if (hint) hint.textContent = "Decoded from the serial - check it against the label.";
        } else if (filledByDecode) {
          made.value = "";
          filledByDecode = false;
          if (hint) hint.textContent = "Couldn't decode this serial - enter the date from the label.";
        }
      } catch (err) {
        // Decoding is a convenience; typing the date still works.
      }
    };
    serial.addEventListener("change", decode);
    brand.addEventListener("change", decode);
    made.addEventListener("input", () => { filledByDecode = false; });
  });
}

// Only offer "also remove it from stock" when the grade being recorded is Retire.
function initRetireToggle() {
  document.querySelectorAll(".js-retire-toggle").forEach((form) => {
    const extra = form.querySelector(".js-retire-only");
    if (!extra) return;
    form.querySelectorAll('input[name="grade"]').forEach((radio) => {
      radio.addEventListener("change", () => {
        extra.hidden = !(radio.checked && radio.value === "retire");
      });
    });
  });
}
