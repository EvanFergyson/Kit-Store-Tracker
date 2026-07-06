document.addEventListener("DOMContentLoaded", () => {
  promoteFlashesToToasts();
  initAjaxBasketAdd();
  initAjaxBasketMutations();
  initQtySteppers();
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
  const badge = document.getElementById("basket-count-badge");
  if (!badge) return;
  if (count > 0) {
    badge.textContent = ` (${count})`;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
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

    const clamp = (value) => {
      const min = parseInt(input.min, 10) || 1;
      if (Number.isNaN(value)) return min;
      return Math.max(min, value);
    };

    down.addEventListener("click", () => {
      input.value = clamp((parseInt(input.value, 10) || 1) - 1);
    });
    up.addEventListener("click", () => {
      input.value = clamp((parseInt(input.value, 10) || 1) + 1);
    });
    input.addEventListener("blur", () => {
      input.value = clamp(parseInt(input.value, 10));
    });
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

// Same idea as initCascade, but filters a list of elements (identified by
// class name) instead of <select> options. Used on the "browse kit" page
// where each item is a table row carrying data-category-id /
// data-subcategory-id rather than being an <option>.
function initListFilter(categorySelectId, subcategorySelectId, rowClassName, noMatchMessageId) {
  const categorySelect = document.getElementById(categorySelectId);
  const subcategorySelect = document.getElementById(subcategorySelectId);
  if (!categorySelect || !subcategorySelect) return;

  const subcategoryOptions = Array.from(subcategorySelect.options);
  const rows = Array.from(document.getElementsByClassName(rowClassName));
  const noMatchMessage = noMatchMessageId ? document.getElementById(noMatchMessageId) : null;

  function applyRowFilter() {
    const categoryId = categorySelect.value;
    const subcategoryId = subcategorySelect.value;
    let visibleCount = 0;
    rows.forEach((row) => {
      const matchesCategory = !categoryId || row.dataset.categoryId === categoryId;
      const matchesSubcategory = !subcategoryId || row.dataset.subcategoryId === subcategoryId;
      const visible = matchesCategory && matchesSubcategory;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    // The server-rendered "no kit in the store" message only covers the case
    // where there's genuinely nothing in the database - it has no idea a
    // client-side filter just hid every row, so without this the table would
    // silently go blank with no explanation when a filter matches nothing.
    if (noMatchMessage) noMatchMessage.hidden = visibleCount !== 0;
  }

  function applyCategoryFilter() {
    const categoryId = categorySelect.value;
    subcategoryOptions.forEach((option) => {
      if (!option.value) return;
      option.hidden = Boolean(categoryId) && option.dataset.categoryId !== categoryId;
    });
    subcategorySelect.value = "";
    applyRowFilter();
  }

  categorySelect.addEventListener("change", applyCategoryFilter);
  subcategorySelect.addEventListener("change", applyRowFilter);
}

// Same idea as initListFilter, but as a row of clickable "chip" buttons
// instead of a <select> dropdown - used on pages where the category is
// already fixed by the page itself (e.g. a per-category request or
// inventory page) and only subcategory needs filtering. One tap picks a
// subcategory directly, rather than opening a dropdown to find it.
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
