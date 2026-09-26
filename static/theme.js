// Light/dark toggle. The page starts in the reader's OS theme; clicking the
// button switches theme and remembers the choice in localStorage. (A tiny
// inline script in <head> re-applies it on every page before it is drawn.)
(function () {
  var button = document.querySelector(".theme-toggle");
  if (!button) return;
  var root = document.documentElement;
  var systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  function current() {
    return root.dataset.theme || (systemDark.matches ? "dark" : "light");
  }

  function label() {
    var next = current() === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", "Switch to " + next + " theme");
    button.title = "Switch to " + next + " theme";
  }

  button.addEventListener("click", function () {
    var next = current() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) {}
    label();
  });

  // Keep the label right if the OS theme changes while the page is open.
  if (systemDark.addEventListener) systemDark.addEventListener("change", label);

  label();
  button.hidden = false;
})();
