// The Translate menu.
//
// Each language in the menu is a link to Google Translate, which works in any
// browser and without JavaScript (only once the site is online).
//
// Browsers with a built-in on-device translator (the Translator API, in
// recent Chrome and Edge on desktop) translate the page in place instead:
// nothing leaves the reader's device. The choice is remembered, so the next
// pages open already translated, and "Show original" undoes it.
(function () {
  var menu = document.querySelector("details.translate");
  if (!menu) return;
  var source = document.documentElement.lang || "en";
  var KEY = "translate-lang";
  var originals = null;      // text node -> original text, to undo
  var bar = null;

  // Close the menu on Escape or when clicking elsewhere.
  document.addEventListener("click", function (e) {
    if (menu.open && !menu.contains(e.target)) menu.open = false;
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); }
  });

  var onDevice = "Translator" in self;

  function languageName(code) {
    var link = menu.querySelector('a[data-lang="' + code + '"]');
    return link ? link.textContent : code;
  }

  // A small bar at the bottom of the screen reporting what's happening.
  function status(message, withUndo) {
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "translate-bar";
      bar.setAttribute("role", "status");
      bar.setAttribute("translate", "no");
      document.body.appendChild(bar);
    }
    bar.textContent = message + " ";
    if (withUndo) {
      var undo = document.createElement("button");
      undo.type = "button";
      undo.textContent = "Show original";
      undo.addEventListener("click", restore);
      bar.appendChild(undo);
    }
  }

  // Every text node worth translating: skips code, scripts, the search
  // window, and anything marked translate="no".
  function textNodes() {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        var el = node.parentElement;
        if (!el || el.closest('script, style, code, pre, dialog, .translate-bar, [translate="no"]')) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    return nodes;
  }

  async function translatePage(target, userClicked) {
    var name = languageName(target);
    try {
      var availability = await Translator.availability({ sourceLanguage: source, targetLanguage: target });
      if (availability === "unavailable") return false;
      // Without a click, don't start a model download the reader didn't ask for.
      if (availability !== "available" && !userClicked) return false;

      status(availability === "available" ? "Translating into " + name + "…" : "Downloading " + name + "…");
      var translator = await Translator.create({
        sourceLanguage: source,
        targetLanguage: target,
        monitor: function (m) {
          m.addEventListener("downloadprogress", function (e) {
            status("Downloading " + name + " (" + Math.round(e.loaded * 100) + "%)…");
          });
        }
      });

      if (originals) restoreText();
      originals = new Map();
      var nodes = textNodes();
      // Translate a few nodes at a time; keep the spaces around each one.
      for (var i = 0; i < nodes.length; i += 8) {
        await Promise.all(nodes.slice(i, i + 8).map(async function (node) {
          var text = node.nodeValue;
          var lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0];
          var out = await translator.translate(text.trim());
          originals.set(node, text);
          node.nodeValue = lead + out + trail;
        }));
      }
      document.documentElement.lang = target;
      try { localStorage.setItem(KEY, target); } catch (e) {}
      status("Translated into " + name + " on your device.", true);
      return true;
    } catch (err) {
      return false;
    }
  }

  function restoreText() {
    if (!originals) return;
    originals.forEach(function (text, node) { node.nodeValue = text; });
    originals = null;
    document.documentElement.lang = source;
  }

  function restore() {
    restoreText();
    try { localStorage.removeItem(KEY); } catch (e) {}
    if (bar) { bar.remove(); bar = null; }
  }

  menu.querySelectorAll("a[data-lang]").forEach(function (link) {
    link.addEventListener("click", function (e) {
      if (!onDevice) return;               // follow the Google Translate link
      e.preventDefault();
      menu.open = false;
      var href = link.href;
      translatePage(link.dataset.lang, true).then(function (ok) {
        if (!ok) location.href = href;     // on-device failed: fall back
      });
    });
  });

  // Carry a chosen language over to the next page.
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  if (saved && onDevice) translatePage(saved, false);
})();
