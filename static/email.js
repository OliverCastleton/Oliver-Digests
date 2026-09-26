// Progressive enhancement for the "Email me about this" link.
// Without JavaScript the link is a plain mailto: with the subject filled in.
// With JavaScript, text the reader has selected in the essay is quoted in
// the email body.
(function () {
  var link = document.querySelector("a.email-link");
  var article = document.querySelector("article .prose");
  var hint = document.querySelector(".email-hint");
  if (!link || !article || !window.getSelection) return;

  var baseHref = link.getAttribute("href");
  var captured = "";
  var MAX = 1500; // keep mailto: URLs a reasonable length

  // The selected text, but only if it lies inside the essay.
  function selectedText() {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return "";
    if (!article.contains(sel.getRangeAt(0).commonAncestorContainer)) return "";
    return sel.toString().trim();
  }

  function quoted(text) {
    if (text.length > MAX) text = text.slice(0, MAX) + "…";
    return "> " + text.replace(/\n+/g, "\n> ") + "\n\n";
  }

  // Show a hint while a passage is selected.
  document.addEventListener("selectionchange", function () {
    if (hint) hint.hidden = !selectedText();
  });

  // Pressing on the link can clear the selection before "click" fires,
  // so remember it on mousedown / touchstart.
  function capture() { captured = selectedText(); }
  link.addEventListener("mousedown", capture);
  link.addEventListener("touchstart", capture, { passive: true });

  link.addEventListener("click", function () {
    var text = selectedText() || captured;
    captured = "";
    link.href = text ? baseHref + "&body=" + encodeURIComponent(quoted(text)) : baseHref;
  });
})();
