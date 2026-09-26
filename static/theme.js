// Light/dark toggle. The page starts in the reader's OS theme; clicking the
// button switches theme and remembers the choice in localStorage. (A tiny
// inline script in <head> re-applies it on every page before it is drawn.)
//
// Where the browser supports view transitions, the new theme spreads out
// in a circle from the button. Readers who prefer reduced motion get an
// instant switch.
(function () {
  var button = document.querySelector(".theme-toggle");
  if (!button) return;
  var root = document.documentElement;
  var systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function current() {
    return root.dataset.theme || (systemDark.matches ? "dark" : "light");
  }

  function label() {
    var next = current() === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", "Switch to " + next + " theme");
    button.title = "Switch to " + next + " theme";
  }

  function apply(theme) {
    root.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch (e) {}
    label();
  }

  button.addEventListener("click", function () {
    var next = current() === "dark" ? "light" : "dark";
    if (!document.startViewTransition || reduceMotion.matches) {
      apply(next);
      return;
    }
    // Grow a circle from the middle of the button to the farthest corner.
    var box = button.getBoundingClientRect();
    var x = box.left + box.width / 2, y = box.top + box.height / 2;
    var radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    root.classList.add("theme-switching");
    var transition = document.startViewTransition(function () { apply(next); });
    transition.ready.then(function () {
      root.animate(
        { clipPath: ["circle(0px at " + x + "px " + y + "px)", "circle(" + radius + "px at " + x + "px " + y + "px)"] },
        { duration: 520, easing: "cubic-bezier(.4, 0, .2, 1)", pseudoElement: "::view-transition-new(root)" }
      );
    });
    transition.finished.finally(function () { root.classList.remove("theme-switching"); });
  });

  // Keep the label right if the OS theme changes while the page is open.
  if (systemDark.addEventListener) systemDark.addEventListener("change", label);

  label();
  button.hidden = false;
})();
