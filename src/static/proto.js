// Prototipas: after a preference change the server answers HX-Refresh (full reload).
// Keyboard focus survives it: the form's data-focus target goes into the URL fragment before
// the request; on load a segmented group focuses its checked radio, the user menu its trigger.
document.addEventListener("htmx:beforeRequest", (event) => {
  const target = event.target.closest("form[data-focus]")?.dataset.focus;
  if (target) history.replaceState(null, "", `#${target}`);
});

document.addEventListener("DOMContentLoaded", () => {
  const target = location.hash ? document.getElementById(location.hash.slice(1)) : null;
  const focusable = target?.matches("details")
    ? target.querySelector("summary")
    : target?.querySelector(".proto-seg-input:checked");
  if (!focusable) return;
  focusable.focus({ preventScroll: true });
  history.replaceState(null, "", location.pathname + location.search);
});

// User menu (<details>): Esc closes it and returns focus to the trigger; a click outside closes it.
document.addEventListener("keydown", (event) => {
  const menu = document.querySelector("details.proto-user[open]");
  if (event.key !== "Escape" || !menu) return;
  menu.removeAttribute("open");
  menu.querySelector("summary").focus();
});

document.addEventListener("click", (event) => {
  const menu = document.querySelector("details.proto-user[open]");
  if (menu && !menu.contains(event.target)) menu.removeAttribute("open");
});
