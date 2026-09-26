"""
End-to-end test: builds a throwaway git repo in a temp folder, commits a
sample essay through several versions, runs build.py on it, and checks the
output. It never touches this repository's own history.

    python tests/test_build.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build.py"

CONFIG = """\
title: Test Site
author: Tester
email: test@example.com
base_url: https://tester.github.io/test-repo
"""


def essay(body, title="On Trust", description="Why trust matters.", publish="true"):
    return f"""---
title: {title}
slug: on-trust
date: 2026-01-10
description: {description}
tags: [ethics, Society]
publish: {publish}
---

{body}
"""


V1 = """Trust is the quiet infrastructure of every society. We rarly notice it.

It is built slowly, one kept promise at a time.

See [[Draft Note]] and [[Other Essay|my other essay]].[^1]

![[diagram.png]]

[^1]: A footnote about trust."""

V2 = """Trust is the quiet infrastructure of every society. We rarely notice it until it fails.

It is built slowly, one kept promise at a time.

A new paragraph arguing that institutions borrow trust from individuals.

See [[Draft Note]] and [[Other Essay|my other essay]].[^1]

![[diagram.png]]

[^1]: A footnote about trust."""

V3 = V2.replace("It is built slowly, one kept promise at a time.",
                "It is built slowly and lost quickly.")


# A second essay that exercises link edge cases: table pipes, heading links,
# code, escaping of unresolved links, and a scalar/duplicate-case tag.
OTHER = r"""---
publish: true
date: 2025-12-01
tags: [2024, ETHICS]
---
Other.

| Link | Image |
|---|---|
| [[On Trust\|trust essay]] | ![[diagram.png\|200]] |

Jump to [[#Some Heading]]. Secret: [[Draft Note|<b>bold</b> 1. Intro]]
I reviewed [[The Idiot]] and [[Short Book]].

````
```
[[On Trust]] inside a four-backtick fence
```
````

Inline `[[On Trust]]` code, and ``double `tick` [[On Trust]]`` too.
"""


class Repo:
    """A temporary git repo with fixed commit dates, so results are deterministic."""

    def __init__(self, path: Path):
        self.path = path
        self.day = 0
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        env = dict(os.environ)
        stamp = f"2026-02-{self.day + 1:02d}T12:00:00+00:00"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args],
                       cwd=self.path, check=True, env=env, capture_output=True)

    def write(self, rel, text):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message):
        self.day += 1
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


def prose(page: str) -> str:
    """Just the rendered note body of a page."""
    return page.split('class="column prose"', 1)[1].split("</div>", 1)[0]


def read(site: Path, rel: str) -> str:
    return (site / rel).read_text(encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repo(Path(tmp))
        repo.write("config.yaml", CONFIG)
        repo.write("vault/.obsidian/app.json", "{}")
        repo.write("vault/Draft Note.md", "---\npublish: false\n---\nPrivate draft.\n")
        repo.write("vault/Home.md", "Hello, I write about [[On Trust|trust]].\n")
        repo.write("vault/Other Essay.md", OTHER)
        (repo.path / "vault/attachments").mkdir(parents=True)
        (repo.path / "vault/attachments/diagram.png").write_bytes(b"\x89PNG fake")
        (repo.path / "vault/attachments/idiot.jpg").write_bytes(b"fake jpeg")
        # Books: one with notes and a cover, one without notes, one unpublished.
        repo.write("vault/books/The Idiot.md", "---\nauthor: Fyodor Dostoevsky\npublished: 1869\n"
                   "finished: 2026-03-02\nrating: 5\ncover: \"[[idiot.jpg]]\"\n"
                   "tags: [novels, Ethics]\npublish: true\n---\nA *great* novel. See [[On Trust]].\n")
        repo.write("vault/books/Short Book.md", "---\nauthor: A. Writer\nfinished: 2025-11-20\n"
                   "rating: 3/5\npublish: true\n---\n")
        repo.write("vault/books/Secret Book.md", "---\npublish: false\n---\nPrivate.\n")
        # Templates may say publish: true but must never be built.
        repo.write("vault/templates/Book.md", "---\ntitle:\npublish: true\n---\n")

        # 1. A draft commit (publish: false) must never become a version.
        repo.write("vault/essays/trust.md", essay("Early secret draft.", publish="false"))
        repo.commit("draft")
        # 2. First published commit -> v1.
        repo.write("vault/essays/trust.md", essay(V1))
        repo.commit("Publish essay on trust")
        # 3. Typo fix: not a version, but appears in v2's diff.
        repo.write("vault/essays/trust.md", essay(V1.replace("rarly", "rarely")))
        repo.commit("fix typo")
        # 4. rev: -> v2.
        repo.write("vault/essays/trust.md", essay(V2))
        repo.commit("rev: Expand the opening and add a paragraph on institutions")
        # 5. Rename the file: history must follow it.
        repo.git("mv", "vault/essays/trust.md", "vault/essays/On Trust.md")
        repo.commit("Rename file")
        # 6. Unpublish, then republish, with a frontmatter-only change.
        repo.write("vault/essays/On Trust.md", essay(V2, publish="false"))
        repo.commit("rev: this is unpublished so it must not count")
        repo.write("vault/essays/On Trust.md", essay(V2, description="Changed description."))
        repo.commit("republish")
        # 7. rev: -> v3.
        repo.write("vault/essays/On Trust.md", essay(V3, description="Changed description."))
        repo.commit("rev: Sharpen the second paragraph")
        # 8. Edit on a branch with a non-rev message, merge with a rev: merge commit -> v4.
        repo.git("checkout", "-q", "-b", "edit")
        repo.write("vault/essays/On Trust.md",
                   essay(V3.replace("institutions borrow", "institutions quietly borrow"),
                         description="Changed description."))
        repo.commit("wip")
        repo.git("checkout", "-q", "main")
        repo.day += 1
        repo.git("merge", "-q", "--no-ff", "-m", "rev: Merged a small edit", "edit")

        result = subprocess.run([sys.executable, str(BUILD), "--config",
                                 str(repo.path / "config.yaml")],
                                capture_output=True, text=True)
        print(result.stdout, result.stderr)
        assert result.returncode == 0, "build failed"
        site = repo.path / "_site"

        checks = []

        def check(name, cond):
            checks.append((name, bool(cond)))

        live = read(site, "on-trust/index.html")
        history = read(site, "on-trust/history/index.html")
        diff2 = read(site, "on-trust/diff/2/index.html")
        diff3 = read(site, "on-trust/diff/3/index.html")
        v1 = read(site, "on-trust/v/1/index.html")
        v2 = read(site, "on-trust/v/2/index.html")

        # Versions
        check("exactly 4 versions", (site / "on-trust/v/4").is_dir()
              and not (site / "on-trust/v/5").exists())
        check("rev: merge commit is a version",
              "Merged a small edit" in history
              and re.search(r"<ins>\s*quietly\s*</ins>", read(site, "on-trust/diff/4/index.html")))
        check("history lists all change notes",
              "Expand the opening" in history and "Sharpen the second" in history
              and "First published." in history)
        check("unpublished rev: commit ignored", "must not count" not in history)
        check("draft never exposed", not any("Early secret draft" in p.read_text("utf-8")
                                            for p in site.rglob("*.html")))
        check("v1 has the original typo", "rarly" in v1)
        check("v2 has the typo fix", "rarely notice it until it fails" in v2)
        check("live page is HEAD content", "built slowly and lost quickly" in live)

        # Diffs
        check("diff 2 shows the typo fix", "<del>rarly</del>" in diff2 and "<ins>rarely</ins>" in diff2)
        check("diff 2 shows the added paragraph",
              re.search(r'class="diff-added"><ins>A new paragraph', diff2))
        check("diff 2 has the change note", "Expand the opening and add a paragraph" in diff2)
        check("diff 3 word-level change",
              "<del>" in diff3 and "<ins>" in diff3 and "lost quickly" in diff3)
        check("diffs exclude frontmatter", "description:" not in diff3 and "publish:" not in diff3)
        check("unchanged paragraph shown plain",
              'class="diff-same">Trust is the quiet' in diff3)

        # Rendering
        check("link to published essay", 'href="/test-repo/other-essay/">my other essay</a>' in live)
        check("link to unpublished note is plain text",
              "Draft Note" in live and "draft-note" not in live)
        check("image copied and embedded", (site / "attachments/diagram.png").exists()
              and 'src="/test-repo/attachments/diagram.png"' in live)
        check("footnote rendered", 'class="footnotes"' in live)
        check("mailto subject", "mailto:test@example.com?subject=Re%3A%20On%20Trust%20%28v4%29" in live)
        check("old version mailto subject", "Re%3A%20On%20Trust%20%28v2%29" in v2)
        check("history link on live page", 'href="/test-repo/on-trust/history/"' in live)

        # Single-version essay: no history
        other = read(site, "other-essay/index.html")
        check("no history link for v1-only essay", "history/" not in other)
        check("no history pages for v1-only essay", not (site / "other-essay/history").exists())
        check("link with escaped table pipe", 'href="/test-repo/on-trust/">trust essay</a>' in other)
        check("image with escaped table pipe", 'width="200"' in other)
        check("[[#Heading]] keeps its text", "Jump to Some Heading." in other)
        check("unresolved link is escaped plain text",
              "&lt;b&gt;bold&lt;/b&gt; 1. Intro" in other and "<ol" not in prose(other))
        check("no links rewritten inside code",
              other.count("[[On Trust]]") == 3)

        # Tags: scalar values work, case variants share a page.
        check("numeric tag page", (site / "tags/2024/index.html").exists())
        ethics = read(site, "tags/ethics/index.html")
        check("case-insensitive tag merge", "On Trust" in ethics and "Other Essay" in ethics
              and len(list((site / "tags").iterdir())) == 5)  # 2024 ethics novels society index

        # Library
        library = read(site, "library/index.html")
        idiot = read(site, "library/the-idiot/index.html")
        check("library lists books, newest first",
              library.index("The Idiot") < library.index("Short Book"))
        check("library groups by year", ">2026</h2>" in library and ">2025</h2>" in library)
        check("unpublished book hidden", "Secret Book" not in library)
        check("book without notes has no page", not (site / "library/short-book").exists()
              and 'href="/test-repo/library/short-book/"' not in library)
        check("book cover copied", (site / "attachments/idiot.jpg").exists()
              and 'src="/test-repo/attachments/idiot.jpg"' in idiot)
        check("book rating stars", "★★★★★" in idiot and "★★★☆☆" in library)
        check("book notes rendered with links", "<em>great</em>" in idiot
              and 'href="/test-repo/on-trust/"' in idiot)
        check("wikilink to book page", 'href="/test-repo/library/the-idiot/">The Idiot</a>' in other)
        check("wikilink to book without notes is text", "and Short Book." in other)
        check("book email subject", "Re%3A%20your%20notes%20on%20The%20Idiot" in idiot)
        check("tag page includes books", "The Idiot" in ethics
              and (site / "tags/novels/index.html").exists())
        check("books are not essays", not (site / "the-idiot").exists())
        check("templates folder never built", not (site / "book").exists())

        # Theme toggle and fonts
        check("theme toggle and script", 'class="theme-toggle icon-button"' in live and "theme.js" in live
              and (site / "theme.js").exists())
        check("fonts copied", (site / "fonts/newsreader-latin-opsz-normal.woff2").exists())

        # Index, tags, exclusions
        essays_page = read(site, "essays/index.html")
        check("essays page newest first",
              essays_page.index("On Trust") < essays_page.index("Other Essay"))

        # Homepage
        home = read(site, "index.html")
        check("home intro from Home.md",
              'Hello, I write about <a href="/test-repo/on-trust/">trust</a>' in home)
        check("Home.md is not an essay", not (site / "home").exists())
        check("home lists essays, books and topics",
              "On Trust" in home and "b-the-idiot" in home and "/tags/novels/" in home)

        # Search
        docs = json.loads(read(site, "search.json"))
        by_title = {d["title"]: d for d in docs}
        check("search index has essays and books",
              by_title["On Trust"]["kind"] == "Essay" and by_title["The Idiot"]["kind"] == "Book")
        check("search index has plain text",
              "built slowly and lost quickly" in by_title["On Trust"]["text"]
              and "<" not in by_title["On Trust"]["text"])
        check("search links books without notes to the library",
              by_title["Short Book"]["url"] == "/test-repo/library/#short-book")
        check("search index excludes unpublished",
              "Draft Note" not in by_title and "Secret Book" not in by_title)
        check("search page and window", (site / "search/index.html").exists()
              and 'class="search-dialog"' in live and (site / "search.js").exists())

        # Translate menu: Google Translate links for the current page
        check("translate links", "translate.google.com/translate?sl=en&amp;tl=it&amp;u="
              "https%3A//tester.github.io/test-repo/on-trust/" in live
              and (site / "translate.js").exists())
        check("tag pages", (site / "tags/ethics/index.html").exists()
              and (site / "tags/society/index.html").exists())
        check("unpublished note not built", not (site / "draft-note").exists())
        check("static files copied", (site / "style.css").exists() and (site / "email.js").exists())

        width = max(len(n) for n, _ in checks)
        for name, ok in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}")
        failed = [n for n, ok in checks if not ok]
        if failed:
            # Leave a copy of the failing diff output for inspection.
            print("\n--- diff/2 ---\n", diff2[diff2.find('<div class="diff">'):][:3000])
            sys.exit(f"\n{len(failed)} check(s) failed")
        print(f"\nAll {len(checks)} checks passed.")

    # A brand-new repo with no commits must still build (every essay is v1).
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repo(Path(tmp))
        repo.write("config.yaml", CONFIG)
        repo.write("vault/New.md", "---\npublish: true\n---\nHello.\n")
        result = subprocess.run([sys.executable, str(BUILD), "--config",
                                 str(repo.path / "config.yaml")], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (repo.path / "_site/new/index.html").exists()
        print("Repo with no commits: PASS")


if __name__ == "__main__":
    main()
