// Site search, entirely in the browser.
//
// The build writes search.json: every essay and book with its plain text.
// It is downloaded the first time someone searches. Matching rules:
//   - every word you type must appear somewhere (title, tags, description or text);
//   - words match the start of words, so "trus" finds "trust";
//   - accents and case are ignored, so "cafe" finds "Café";
//   - title matches count most, then tags, then description, then the text.
//
// The Search link opens a search window. "/" or Ctrl+K (Cmd+K) opens it from
// anywhere. On the /search/ page the box is part of the page. Without
// JavaScript the Search link leads to /search/, which says search needs JS.
(function () {
  var base = document.documentElement.dataset.base || "";
  var dialog = document.querySelector(".search-dialog");
  var index = null;          // the loaded search.json, prepared for matching
  var loading = null;        // the pending fetch, so we only download once

  // Lower-case and strip accents *character by character*, so positions in
  // the folded text match positions in the original (used for snippets).
  function fold(text) {
    var out = "";
    for (var i = 0; i < text.length; i++) {
      var c = text[i].normalize("NFD")[0].toLowerCase();
      out += c.length === 1 ? c : text[i];
    }
    return out;
  }

  function load() {
    if (index) return Promise.resolve(index);
    if (!loading) {
      loading = fetch(base + "/search.json")
        .then(function (r) { return r.json(); })
        .then(function (docs) {
          index = docs.map(function (d) {
            d.f = {
              title: fold(d.title),
              tags: fold(d.tags.join(" ")),
              detail: fold(d.detail),
              text: fold(d.text)
            };
            return d;
          });
          return index;
        });
    }
    return loading;
  }

  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // A regex matching `word` at the start of a word.
  function wordStart(word, flags) {
    return new RegExp("(^|[^\\p{L}\\p{N}])" + escapeRegExp(word), "u" + (flags || ""));
  }

  function search(query) {
    var words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    var patterns = words.map(function (w) { return wordStart(w); });
    var results = [];
    index.forEach(function (doc) {
      var score = 0;
      for (var i = 0; i < patterns.length; i++) {
        var p = patterns[i], s = 0;
        if (p.test(doc.f.title)) s += 12;
        if (p.test(doc.f.tags)) s += 6;
        if (p.test(doc.f.detail)) s += 4;
        var hits = doc.f.text.match(wordStart(words[i], "g"));
        if (hits) s += Math.min(hits.length, 5);
        if (!s) return;          // every word must match somewhere
        score += s;
      }
      results.push({ doc: doc, score: score });
    });
    results.sort(function (a, b) {
      return b.score - a.score || (b.doc.date > a.doc.date ? 1 : -1);
    });
    return results.slice(0, 20).map(function (r) { return r.doc; });
  }

  // Append `text` to `el`, wrapping matched words in <mark>. Built with DOM
  // nodes (never innerHTML), so text from notes can't inject markup.
  function appendMarked(el, text, words) {
    var folded = fold(text);
    var ranges = [];
    words.forEach(function (w) {
      var re = wordStart(w, "g"), m;
      while ((m = re.exec(folded))) {
        var start = m.index + m[1].length;
        ranges.push([start, start + w.length]);
        if (re.lastIndex === m.index) re.lastIndex++;
      }
    });
    ranges.sort(function (a, b) { return a[0] - b[0]; });
    var pos = 0;
    ranges.forEach(function (r) {
      if (r[0] < pos) return;    // overlapping match
      el.appendChild(document.createTextNode(text.slice(pos, r[0])));
      var mark = document.createElement("mark");
      mark.textContent = text.slice(r[0], r[1]);
      el.appendChild(mark);
      pos = r[1];
    });
    el.appendChild(document.createTextNode(text.slice(pos)));
  }

  // About 180 characters of the text around the first matching word.
  function snippet(doc, words) {
    var first = -1;
    words.forEach(function (w) {
      var m = doc.f.text.match(wordStart(w));
      if (m) {
        var at = m.index + m[1].length;
        if (first < 0 || at < first) first = at;
      }
    });
    if (first < 0) return doc.detail || doc.text.slice(0, 180);
    var start = Math.max(0, doc.text.lastIndexOf(" ", Math.max(0, first - 60)));
    var end = Math.min(doc.text.length, start + 180);
    return (start > 0 ? "…" : "") + doc.text.slice(start, end).trim() + (end < doc.text.length ? "…" : "");
  }

  function render(list, query) {
    list.textContent = "";
    var words = fold(query).split(/\s+/).filter(Boolean);
    if (!words.length) return;
    var found = search(query);
    if (!found.length) {
      var empty = document.createElement("li");
      empty.className = "search-empty";
      empty.textContent = "Nothing matches “" + query.trim() + "”. Try fewer or shorter words.";
      list.appendChild(empty);
      return;
    }
    found.forEach(function (doc, i) {
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = doc.url;
      a.id = list.id + "-" + i;
      var kind = document.createElement("span");
      kind.className = "result-kind";
      kind.textContent = doc.kind === "Book" && doc.detail ? "Book by " + doc.detail : doc.kind;
      var title = document.createElement("span");
      title.className = "result-title";
      appendMarked(title, doc.title, words);
      var text = document.createElement("span");
      text.className = "result-snippet";
      appendMarked(text, snippet(doc, words), words);
      a.append(title, kind, text);
      li.appendChild(a);
      list.appendChild(li);
    });
  }

  // Wire up one search box (in the dialog, or on the /search/ page).
  function connect(root) {
    var input = root.querySelector(".search-input");
    var list = root.querySelector(".search-results");
    if (!input || !list) return null;
    list.id = list.id || "results-" + Math.random().toString(36).slice(2, 8);
    var active = -1;

    function links() { return list.querySelectorAll("a"); }

    function select(i) {
      var all = links();
      if (!all.length) return;
      active = (i + all.length) % all.length;
      all.forEach(function (a, n) { a.setAttribute("aria-selected", n === active ? "true" : "false"); });
      all[active].scrollIntoView({ block: "nearest" });
    }

    function update() {
      load().then(function () {
        render(list, input.value);
        active = -1;
      }).catch(function () {
        list.textContent = "";
        var li = document.createElement("li");
        li.className = "search-empty";
        li.textContent = "Search couldn’t load. Check your connection and try again.";
        list.appendChild(li);
      });
    }

    input.addEventListener("input", update);
    // Arrow keys move through results; Enter opens the selected one.
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); select(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); select(active - 1); }
      else if (e.key === "Enter") {
        var target = links()[active < 0 ? 0 : active];
        if (target) { e.preventDefault(); target.click(); }
      }
    });
    return { input: input, update: update };
  }

  // The /search/ page: the box is on the page, and ?q= fills it in.
  var page = document.querySelector(".search-page");
  var pageBox = page && connect(page);
  if (pageBox) {
    var q = new URLSearchParams(location.search).get("q");
    if (q) { pageBox.input.value = q; pageBox.update(); }
    pageBox.input.focus();
  }

  // The search window, everywhere else.
  var dialogBox = dialog && dialog.showModal && connect(dialog);
  if (!dialogBox) return;

  function open() {
    if (pageBox) { pageBox.input.focus(); return; }
    if (!dialog.open) dialog.showModal();
    dialogBox.input.select();
    load();  // start downloading the index while the reader types
  }

  document.querySelectorAll(".search-open").forEach(function (link) {
    link.addEventListener("click", function (e) { e.preventDefault(); open(); });
  });
  document.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
      e.preventDefault();
      open();
    }
  });
  dialog.querySelector(".search-close").addEventListener("click", function () { dialog.close(); });
  // Clicking the dimmed area outside the window closes it.
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
})();
