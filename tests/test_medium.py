"""
Tests for medium_sync.py that need no browser and no Medium account: rendering
for Medium, change detection, and the sync state on a git branch (pushed to a
throwaway bare repo standing in for GitHub).

    python tests/test_medium.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402
import medium_sync as ms  # noqa: E402

CONFIG = """\
title: Test Site
author: Tester
email: test@example.com
base_url: https://tester.github.io/test-repo
medium:
  book_title: "Reading notes: {title}"
"""

ESSAY = """---
title: On Trust
slug: on-trust
date: 2026-01-10
description: Why trust matters.
tags: [ethics, society, a, b, c, d]
publish: true
---
# On Trust

Trust is ==quiet== infrastructure.[^1] See [[Short Book]] and [[Draft]].

## A section

> First line.
>
> Second line.

| Name | Value |
|---|---|
| a & b | 1 |

![[diagram.png]]

[^1]: A footnote.
"""

BOOK = """---
title: Short Book
author: Ann Author
published: 1999
finished: 2026-03-02
rating: 4
cover: "[[cover.jpg]]"
publish: true
---
Good book.
"""

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    checks.append((name, bool(ok)))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config.yaml").write_text(CONFIG, encoding="utf-8")
        vault = root / "vault"
        (vault / "books").mkdir(parents=True)
        (vault / "attachments").mkdir()
        (vault / "attachments/diagram.png").write_bytes(b"png")
        (vault / "attachments/cover.jpg").write_bytes(b"jpg")
        (vault / "On Trust.md").write_text(ESSAY, encoding="utf-8")
        (vault / "Draft.md").write_text("---\npublish: false\n---\nx\n", encoding="utf-8")
        (vault / "books/Short Book.md").write_text(BOOK, encoding="utf-8")
        (vault / "books/No Notes.md").write_text("---\npublish: true\n---\n", encoding="utf-8")

        cfg = build.load_config(root / "config.yaml", local=False)
        mcfg = ms.medium_config(cfg)
        items = {i.key: i for i in ms.load_items(cfg, root, mcfg)}

        check("essay and book with notes are items; book without notes isn't",
              set(items) == {"essay:on-trust", "book:short-book"})
        essay, book = items["essay:on-trust"], items["book:short-book"]
        h = essay.html
        check("essay url is absolute", essay.url == "https://tester.github.io/test-repo/on-trust/")
        check("wikilink is absolute",
              'href="https://tester.github.io/test-repo/library/short-book/"' in h)
        check("unpublished link is plain text", "Draft" in h and "/draft/" not in h)
        check("image src is absolute",
              'src="https://tester.github.io/test-repo/attachments/diagram.png"' in h)
        check("repeated # title dropped; h2 -> h3", "<h3>A section</h3>" in h
              and "On Trust</h" not in h)
        check("description becomes subtitle", h.startswith("<h4>Why trust matters.</h4>"))
        check("table -> preformatted text", "<table" not in h and "<pre>Name | Value\na &amp; b | 1</pre>" in h)
        check("footnote ref flattened", "infrastructure.[1]" in h and "fnref" not in h)
        check("footnotes section kept as Notes", "<h4>Notes</h4>" in h and "A footnote." in h)
        check("quote stays one block, lines split by <br>",
              "<blockquote>First line.<br><br>Second line.</blockquote>" in h)
        check("highlight unwrapped", "<mark>" not in h and "quiet" in h)
        check("footer links back", 'Originally published at <a href="https://tester.github.io/'
              'test-repo/on-trust/">Test Site</a>' in h)
        check("story html starts with title", essay.story_html.startswith("<h3>On Trust</h3>"))
        check("book title from config", book.title == "Reading notes: Short Book")
        check("book facts", "<strong>Short Book</strong> by Ann Author (1999)" in book.html
              and "★★★★☆" in book.html and "Finished" in book.html)
        check("book cover absolute", 'src="https://tester.github.io/test-repo/attachments/cover.jpg"'
              in book.html)

        # Fingerprints: stable across loads, change with the text or title.
        again = {i.key: i for i in ms.load_items(cfg, root, mcfg)}
        check("digest is stable", again["essay:on-trust"].digest == essay.digest)
        (vault / "On Trust.md").write_text(ESSAY.replace("quiet", "silent"), encoding="utf-8")
        edited = {i.key: i for i in ms.load_items(cfg, root, mcfg)}
        check("digest changes with text", edited["essay:on-trust"].digest != essay.digest)
        check("only the first five tags count",
              ms.Item("k", "t", "u", list("abcdef"), "x").digest
              == ms.Item("k", "t", "u", list("abcdeZ"), "x").digest)

        # Planning.
        state = {"posts": {
            "essay:on-trust": {"post_id": "aaa111", "hash": essay.digest},
            "book:short-book": {"post_id": "bbb222", "hash": book.digest},
            "essay:removed": {"post_id": "ccc333", "hash": "x"},
        }}
        create, update, same = ms.plan(list(edited.values()), state)
        check("plan: edited essay updates, book unchanged",
              [i.key for i in update] == ["essay:on-trust"] and not create
              and [i.key for i in same] == ["book:short-book"])
        check("plan: unknown note is created",
              [i.key for i in ms.plan(list(edited.values()), ms.empty_state())[0]]
              == ["essay:on-trust", "book:short-book"])
        state["posts"]["book:short-book"]["hash"] = ""
        check("plan: empty hash (unconfirmed draft) updates, never re-creates",
              "book:short-book" in [i.key for i in ms.plan(list(edited.values()), state)[1]])
        check("force updates everything",
              len(ms.plan(list(edited.values()), state, force=True)[1]) == 2)
        check("gone lists notes no longer on the site",
              ms.gone(list(edited.values()), state) == ["essay:removed"])

        # Export mode writes one file per story.
        out = root / "export"
        r = subprocess.run([sys.executable, str(Path(ms.__file__)), "--config",
                            str(root / "config.yaml"), "--export", str(out)],
                           capture_output=True, text=True)
        check("--export writes files", r.returncode == 0 and
              sorted(p.name for p in out.iterdir()) == ["book-short-book.html",
                                                        "essay-on-trust.html"])

    # Cookies.
    c = ms.parse_cookies("sid=1:abc; uid=42; other=x")
    check("cookie string parsed", {x["name"]: x["value"] for x in c}
          == {"sid": "1:abc", "uid": "42", "other": "x"} and c[0]["domain"] == ".medium.com")
    c = ms.parse_cookies(json.dumps([{"name": "sid", "value": "s"}, {"name": "uid", "value": "u"}]))
    check("cookie JSON parsed", len(c) == 2)
    try:
        ms.parse_cookies("sid=only")
        check("missing uid rejected", False)
    except ms.MediumError:
        check("missing uid rejected", True)

    # State on a branch of a remote, without touching the working tree.
    with tempfile.TemporaryDirectory() as tmp:
        remote, work = Path(tmp, "remote.git"), Path(tmp, "work")
        git(Path(tmp), "init", "-q", "--bare", str(remote))
        git(Path(tmp), "init", "-q", "-b", "main", str(work))
        (work / "a.txt").write_text("a", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "init")
        git(work, "remote", "add", "origin", str(remote))
        head = git(work, "rev-parse", "HEAD")

        store = ms.StateStore(work, "medium-state", None)
        check("branch state: fresh when branch missing", store.load() == ms.empty_state())
        s1 = {"posts": {"essay:x": {"post_id": "abc123", "hash": "h1"}}}
        store.save(s1, "one")
        s1["posts"]["essay:y"] = {"post_id": "def456", "hash": "h2"}
        store.save(s1, "two")
        fresh = ms.StateStore(work, "medium-state", None)
        check("branch state: round trip", fresh.load() == s1)
        log = git(remote, "log", "--format=%s", "medium-state").split()
        check("branch state: one commit per save, linear", log == ["two", "one"])
        check("branch state: working tree and main untouched",
              git(work, "rev-parse", "HEAD") == head and
              git(work, "status", "--porcelain") == "" and
              git(work, "branch", "--show-current").strip() == "main")
        # A stale writer must not overwrite newer state.
        stale = ms.StateStore(work, "medium-state", None)
        stale.parent = git(remote, "rev-list", "--max-parents=0", "medium-state").strip()
        try:
            stale.save({"posts": {}}, "stale")
            check("branch state: stale push refused", False)
        except RuntimeError:
            check("branch state: stale push refused", True)

        file_store = ms.StateStore(work, None, Path(tmp, "state.json"))
        file_store.save(s1, "x")
        check("file state: round trip", file_store.load() == s1)

    width = max(len(n) for n, _ in checks)
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}")
    failed = [n for n, ok in checks if not ok]
    if failed:
        sys.exit(f"\n{len(failed)} check(s) failed")
    print(f"\nAll {len(checks)} checks passed.")


if __name__ == "__main__":
    main()
