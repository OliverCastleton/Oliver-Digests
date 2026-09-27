#!/usr/bin/env python3
"""
medium_sync.py: mirror the site's essays and book notes to Medium.

Usage
    python medium_sync.py --dry-run          # list what would be posted or updated
    python medium_sync.py --export out_dir   # write Medium-ready HTML files; no browser
    python medium_sync.py                    # sync (needs the MEDIUM_COOKIES variable)
    python medium_sync.py --link essay:on-trust=1a2b3c4d5e6f
                                             # adopt a story already on Medium

Why a browser
    Medium's API only ever created stories (it can't edit them), and it stopped
    issuing tokens in 2023. So this script drives a real Chrome, logged in with
    your Medium session cookies, and uses the Medium editor like you would:
    paste the story in, then press Publish.

    Medium can change its editor at any time; when that happens this script
    stops with a message and a screenshot. Every Medium-specific selector is
    in the MediumSession class, so fixes happen in one place. Automating the
    editor may be against Medium's terms of service; you run it at your own risk.

What happens, in order
    1. Load the published essays and book notes exactly as build.py does.
    2. Render each one as Medium-friendly HTML with absolute links, plus an
       "Originally published at ..." line pointing back to the site.
    3. Compare a fingerprint of each against the saved sync state:
         - not in the state yet  -> create a new Medium story;
         - fingerprint changed   -> replace the story's text and republish;
         - unchanged             -> skip.
    4. Save the state after every story, so a crash never causes a duplicate.

The state (note -> Medium story id) lives in medium-state.json on its own
branch, `medium-state`, so it never adds commits to main. Notes you unpublish
or delete are left on Medium untouched; the script lists them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import build  # the site builder: same notes, same Markdown, same links

HERE = Path(__file__).resolve().parent
STATE_FILE = "medium-state.json"
SCREENSHOTS = HERE / "medium-screenshots"  # written only when something fails


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def medium_config(cfg: dict) -> dict:
    """The `medium:` section of config.yaml, with defaults."""
    m = dict(cfg.get("medium") or {})
    m.setdefault("status", "public")
    m.setdefault("essays", True)
    m.setdefault("books", True)
    m.setdefault("book_title", "Notes on {title}")
    m.setdefault("max_per_run", 10)
    m.setdefault("state_branch", "medium-state")
    if m["status"] not in ("public", "draft"):
        sys.exit("config.yaml: medium.status must be 'public' or 'draft'")
    return m


# ---------------------------------------------------------------------------
# What to post: the notes, rendered for Medium
# ---------------------------------------------------------------------------

@dataclass
class Item:
    key: str          # "essay:<slug>" or "book:<slug>"; the id in the sync state
    title: str        # the Medium story title
    url: str          # the page on the site, absolute
    tags: list[str]   # Medium topics (Medium keeps the first five)
    html: str         # story body, without the title

    @property
    def digest(self) -> str:
        """Fingerprint of everything that ends up on Medium."""
        data = json.dumps([self.title, self.tags[:5], self.html], ensure_ascii=False)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    @property
    def story_html(self) -> str:
        """Title plus body, as pasted into the editor."""
        return f"<h3>{html.escape(self.title)}</h3>\n{self.html}"


# Medium's editor has two heading sizes; everything else is flattened into them.
BIG_HEADING = re.compile(r"<(/?)h[12]\b")
SMALL_HEADING = re.compile(r"<(/?)h[3-6]\b")
TABLE = re.compile(r"<table>.*?</table>", re.DOTALL)
CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.DOTALL)
ROW = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
TAG = re.compile(r"<[^>]+>")
# markdown-it footnotes: a superscript link in the text, a ↩ link after each note.
FOOTNOTE_REF = re.compile(r'<sup class="footnote-ref"><a [^>]*>\[?([^\]<]+)\]?</a></sup>')
FOOTNOTE_BACKREF = re.compile(r'\s*<a href="#fnref[^"]*" class="footnote-backref">.*?</a>')
FOOTNOTE_SECTION = re.compile(r'<hr class="footnotes-sep"\s*/?>\s*<section class="footnotes">'
                              r"(.*?)</section>", re.DOTALL)


def table_to_text(match: re.Match) -> str:
    """Medium has no tables: show each row as a line of a preformatted block."""
    rows = []
    for row in ROW.findall(match.group(0)):
        cells = [html.unescape(TAG.sub("", c)).strip() for c in CELL.findall(row)]
        rows.append(" | ".join(cells))
    return "<pre>" + html.escape("\n".join(rows)) + "</pre>"


def quote_lines(match: re.Match) -> str:
    """Medium turns every <p> in a quote into a quote of its own, plus an empty
    one for the line break after it: keep the quote one block, lines split by <br>."""
    inner = re.sub(r"</p>\s*<p>", "<br><br>", match.group(1).strip())
    return "<blockquote>" + re.sub(r"^<p>|</p>$", "", inner) + "</blockquote>"


def for_medium(content: str) -> str:
    """Adapt build.py's HTML to what Medium's editor keeps when it's pasted in."""
    content = TABLE.sub(table_to_text, content)
    content = re.sub(r"<blockquote>(.*?)</blockquote>", quote_lines, content, flags=re.DOTALL)
    content = re.sub(r">\s*\n\s*<", "><", content)  # line breaks between tags become empty lines
    content = SMALL_HEADING.sub(r"<\1h4", content)  # before h1/h2 become h3
    content = BIG_HEADING.sub(r"<\1h3", content)
    content = FOOTNOTE_REF.sub(r"[\1]", content)
    content = FOOTNOTE_BACKREF.sub("", content)
    content = FOOTNOTE_SECTION.sub(r"<hr>\n<h4>Notes</h4>\n\1", content)
    content = re.sub(r"</?mark>", "", content)       # no highlighter on Medium
    content = re.sub(r' loading="lazy"', "", content)
    return content.strip()


def footer(cfg: dict, url: str, extra: str = "") -> str:
    site = html.escape(cfg["title"])
    return (f'<hr>\n<p><em>Originally published at <a href="{html.escape(url)}">{site}</a>.'
            f"{extra}</em></p>")


def load_items(cfg: dict, config_dir: Path, mcfg: dict) -> list[Item]:
    """Every essay and book note (with notes) that is on the site, rendered for Medium."""
    # With the full base_url as the base path, every link and image is absolute,
    # so it works from Medium. Images load from the deployed site.
    cfg = dict(cfg, base_path=cfg["base_url"])
    vault = (config_dir / cfg["vault"]).resolve()
    root = build.find_repo_root(vault)
    essays = build.load_essays(cfg, vault, root)
    books = build.load_books(cfg, vault)
    md = build.make_markdown()
    items: list[Item] = []
    with tempfile.TemporaryDirectory() as tmp:  # Linker copies images here; unused
        linker = build.Linker(essays, books, vault, Path(tmp), cfg["base_path"])
        if mcfg["essays"]:
            for e in reversed(essays):  # oldest first, so Medium gets them in order
                body = for_medium(build.render_markdown(md, linker, e.body))
                if e.description:
                    body = f"<h4>{html.escape(e.description)}</h4>\n{body}"
                extra = (f' <a href="{html.escape(e.url)}history/">Version history</a>.'
                         if e.has_history else "")
                items.append(Item(f"essay:{e.slug}", e.title, e.url, e.tags,
                                  body + "\n" + footer(cfg, e.url, extra)))
        if mcfg["books"]:
            for b in reversed(books):
                if not b.has_notes:
                    continue  # a book without notes has no page, so nothing to post
                items.append(book_item(cfg, mcfg, b, linker, md))
    return items


def book_item(cfg: dict, mcfg: dict, b: build.Book, linker: build.Linker, md) -> Item:
    facts = []
    if b.author:
        facts.append(f"by {html.escape(b.author)}")
    if b.published:
        facts.append(f"({html.escape(b.published)})")
    line = " ".join(facts)
    details = [x for x in (
        f"Rating: {b.stars}" if b.rating else "",
        f"Finished {build.format_date(b.finished)}" if b.finished else "",
    ) if x]
    head = f"<p><strong>{html.escape(b.title)}</strong> {line}".rstrip() + "</p>"
    if details:
        head += f"\n<p><em>{' · '.join(details)}</em></p>"
    if b.cover:
        cover = linker.asset_url(b.cover)
        if cover:
            head = f'<img src="{html.escape(cover)}" alt="Cover of {html.escape(b.title)}">\n' + head
    if b.description:
        head = f"<h4>{html.escape(b.description)}</h4>\n" + head
    body = for_medium(build.render_markdown(md, linker, b.body))
    title = mcfg["book_title"].format(title=b.title, author=b.author)
    return Item(f"book:{b.slug}", title, b.url, b.tags,
                f"{head}\n{body}\n{footer(cfg, b.url)}")


# ---------------------------------------------------------------------------
# Sync state: which note is which Medium story
# ---------------------------------------------------------------------------
# {"posts": {"essay:on-trust": {"post_id": "1a2b3c", "hash": "...", "url": "...",
#                               "title": "...", "synced": "2026-09-27T10:00:00Z"}}}
# An empty "hash" means the story exists but its content isn't confirmed, so the
# next run updates it (never creates a second copy).

def empty_state() -> dict:
    return {"posts": {}}


def plan(items: list[Item], state: dict, force: bool = False):
    """Split items into (to create, to update, unchanged)."""
    create, update, same = [], [], []
    for item in items:
        entry = state["posts"].get(item.key)
        if entry is None:
            create.append(item)
        elif force or entry.get("hash") != item.digest:
            update.append(item)
        else:
            same.append(item)
    return create, update, same


def gone(items: list[Item], state: dict) -> list[str]:
    """Keys in the state whose note is no longer on the site."""
    keys = {i.key for i in items}
    return sorted(k for k in state["posts"] if k not in keys)


class StateStore:
    """Keeps the state in medium-state.json on a branch of `origin`, or in a plain file.

    Branch mode uses git plumbing (hash-object, mktree, commit-tree, push), so it
    never touches your working tree or current branch.
    """

    def __init__(self, root: Path, branch: str | None, file: Path | None):
        self.root, self.branch, self.file = root, branch, file
        self.parent: str | None = None  # the branch's commit we last read or wrote

    def git(self, *args: str, stdin: str | None = None, check: bool = True):
        env = dict(os.environ)
        for var, value in (("GIT_AUTHOR_NAME", "Medium sync"),
                           ("GIT_AUTHOR_EMAIL", "medium-sync@users.noreply.github.com")):
            env.setdefault(var, value)
            env.setdefault(var.replace("AUTHOR", "COMMITTER"), value)
        # Bytes, not text: on Windows text mode would turn "\n" into "\r\n".
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, env=env,
                                input=stdin.encode("utf-8") if stdin is not None else None)
        out = subprocess.CompletedProcess(result.args, result.returncode,
                                          result.stdout.decode("utf-8", "replace"),
                                          result.stderr.decode("utf-8", "replace"))
        if check and out.returncode:
            raise subprocess.CalledProcessError(out.returncode, out.args, out.stdout, out.stderr)
        return out

    def load(self) -> dict:
        if self.file:
            return json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() \
                else empty_state()
        ref = f"refs/heads/{self.branch}"
        probe = self.git("ls-remote", "--exit-code", "--heads", "origin", ref, check=False)
        if probe.returncode == 2:
            print(f"No '{self.branch}' branch on origin yet: starting a fresh sync state.")
            return empty_state()
        if probe.returncode != 0:
            # Carrying on without the state would post every note a second time.
            sys.exit(f"Can't read the sync state from origin/{self.branch}:\n{probe.stderr}")
        self.git("fetch", "--quiet", "origin", ref)
        self.parent = self.git("rev-parse", "FETCH_HEAD").stdout.strip()
        text = self.git("show", f"{self.parent}:{STATE_FILE}").stdout
        return json.loads(text)

    def save(self, state: dict, message: str) -> None:
        text = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if self.file:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.file)
            return
        blob = self.git("hash-object", "-w", "--stdin", stdin=text).stdout.strip()
        tree = self.git("mktree", stdin=f"100644 blob {blob}\t{STATE_FILE}\n").stdout.strip()
        parents = ["-p", self.parent] if self.parent else []
        commit = self.git("commit-tree", tree, *parents, "-m", message).stdout.strip()
        # Not forced: if someone else moved the branch meanwhile, stop rather than lose data.
        push = self.git("push", "--quiet", "origin", f"{commit}:refs/heads/{self.branch}",
                        check=False)
        if push.returncode != 0:
            raise RuntimeError(f"Couldn't save the sync state to origin/{self.branch}:\n"
                               f"{push.stderr}\nState that wasn't saved:\n{text}")
        self.parent = commit


# ---------------------------------------------------------------------------
# The browser: everything that knows about Medium's web pages
# ---------------------------------------------------------------------------

class MediumError(Exception):
    pass


def parse_cookies(raw: str) -> list[dict]:
    """MEDIUM_COOKIES as `sid=...; uid=...`, or a JSON list exported by a cookie extension."""
    raw = raw.strip()
    if raw.startswith("["):
        pairs = [(c["name"], c["value"]) for c in json.loads(raw)]
    else:
        pairs = [tuple(p.strip().split("=", 1)) for p in raw.split(";") if "=" in p]
    cookies = [{"name": n, "value": v, "domain": ".medium.com", "path": "/",
                "secure": True, "sameSite": "None"} for n, v in pairs]
    if not {"sid", "uid"} <= {c["name"] for c in cookies}:
        raise MediumError("MEDIUM_COOKIES must include both the 'sid' and 'uid' cookies.")
    return cookies


# Dispatches a paste event carrying HTML at the focused element, as if you'd
# pressed Ctrl+V. Returns true when the editor handled it.
PASTE_JS = """([html, text]) => {
  const data = new DataTransfer();
  data.setData('text/html', html);
  data.setData('text/plain', text);
  const event = new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true});
  (document.activeElement || document.body).dispatchEvent(event);
  return event.defaultPrevented;
}"""

# The same through the real clipboard, for when the synthetic event is ignored.
CLIPBOARD_JS = """async ([html, text]) => {
  await navigator.clipboard.write([new ClipboardItem({
    'text/html': new Blob([html], {type: 'text/html'}),
    'text/plain': new Blob([text], {type: 'text/plain'}),
  })]);
}"""

STORY_ID = re.compile(r"/p/([0-9a-f]{6,})/edit")


class MediumSession:
    ORIGIN = "https://medium.com"
    EDITOR = 'main [contenteditable="true"], [contenteditable="true"]'

    def __init__(self, cookies: list[dict], headed: bool, channel: str | None):
        self.cookies, self.headed, self.channel = cookies, headed, channel

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.launch(
                headless=not self.headed, channel=self.channel,
                args=["--disable-blink-features=AutomationControlled"])
        except Exception:
            if not self.channel:
                raise
            print(f"  '{self.channel}' not available; using Playwright's Chromium.")
            self.browser = self._pw.chromium.launch(headless=not self.headed)
        self.context = self.browser.new_context(locale="en-US",
                                                viewport={"width": 1280, "height": 900})
        self.context.grant_permissions(["clipboard-read", "clipboard-write"],
                                       origin=self.ORIGIN)
        self.context.add_cookies(self.cookies)
        self.page = self.context.new_page()
        self.page.set_default_timeout(30_000)
        return self

    def __exit__(self, *exc):
        self.browser.close()
        self._pw.stop()

    def screenshot(self, name: str) -> Path:
        SCREENSHOTS.mkdir(exist_ok=True)
        path = SCREENSHOTS / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^\w-]', '_', name)}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass
        return path

    # -- navigation ----------------------------------------------------------

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")
        # Cloudflare's "Just a moment..." check usually clears by itself;
        # its "Attention Required!" page is a block and doesn't.
        for _ in range(30):
            title = self.page.title().lower()
            if "attention required" in title:
                break
            if "just a moment" not in title:
                return
            time.sleep(1)
        raise MediumError("Blocked by Medium's bot check (Cloudflare). Try again later, "
                          "or run the sync from your own computer.")

    def check_login(self) -> None:
        self.goto(f"{self.ORIGIN}/me/stories/drafts")
        try:
            self.page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass  # pages with endless background requests never go idle; check anyway
        if "signin" in self.page.url or "/m/signin" in self.page.url or \
                self.page.get_by_role("link", name=re.compile(r"^Sign in$")).count():
            raise MediumError("Not logged in to Medium: the MEDIUM_COOKIES are missing or "
                              "expired. Copy fresh 'sid' and 'uid' cookies (see README).")

    # -- writing -------------------------------------------------------------

    def _editor(self):
        editor = self.page.locator(self.EDITOR).first
        editor.wait_for(state="visible", timeout=60_000)
        return editor

    def _paste(self, item: Item) -> None:
        text = build.html_to_text(item.story_html)
        handled = self.page.evaluate(PASTE_JS, [item.story_html, text])
        if not handled:
            self.page.evaluate(CLIPBOARD_JS, [item.story_html, text])
            self.page.keyboard.press("ControlOrMeta+V")
        # Check the text really arrived before anything is published.
        probe = re.sub(r"\s+", " ", build.html_to_text(item.html)).strip()[:40]
        try:
            self.page.wait_for_function(
                "([sel, probe]) => [...document.querySelectorAll(sel)].some("
                "e => e.innerText.replace(/\\s+/g, ' ').includes(probe))",
                arg=[self.EDITOR, probe], timeout=20_000)
        except Exception:
            raise MediumError("The story text didn't appear in Medium's editor after pasting.")

    def _replace_text(self, item: Item) -> None:
        editor = self._editor()
        editor.click()
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.press("Backspace")
        self._paste(item)

    def _wait_saved(self) -> None:
        """Medium autosaves; wait until the status in the top bar says so."""
        time.sleep(2)
        try:
            self.page.get_by_text(re.compile(r"^Saved$")).first.wait_for(timeout=30_000)
        except Exception:
            time.sleep(8)  # no status label found: give the autosave time anyway

    def new_story(self, item: Item) -> str:
        """Create a draft with the item's text. Returns the Medium story id."""
        self.goto(f"{self.ORIGIN}/new-story")
        self._replace_text(item)
        self.page.wait_for_url(STORY_ID, timeout=60_000)  # the draft got an id: it's saved
        self._wait_saved()
        return STORY_ID.search(self.page.url).group(1)

    def edit_story(self, post_id: str, item: Item) -> None:
        self.goto(f"{self.ORIGIN}/p/{post_id}/edit")
        if "/edit" not in self.page.url:
            raise MediumError(f"Story {post_id} can't be edited (deleted, or another account?).")
        self._replace_text(item)
        self._wait_saved()

    def publish(self, item: Item, first_time: bool) -> str | None:
        """Press Publish (or "Save and publish"). Returns the public URL if Medium shows it."""
        button = self.page.get_by_role("button", name=re.compile(r"^(Publish|Save and publish)$"))
        button.first.click()
        confirm = self.page.get_by_role("button", name=re.compile(r"^Publish now$"))
        try:
            confirm.first.wait_for(timeout=15_000)
        except Exception:
            confirm = None  # updates are often published without a dialog
        if confirm is not None:
            if first_time:
                self._add_topics(item.tags[:5])
            confirm.first.click()
        try:
            self.page.wait_for_url(lambda u: "/edit" not in u and "new-story" not in u,
                                   timeout=60_000)
            return self.page.url.split("?")[0]
        except Exception:
            return None

    def _add_topics(self, tags: list[str]) -> None:
        if not tags:
            return
        box = self.page.get_by_placeholder(re.compile("topic", re.I))
        if not box.count():
            box = self.page.locator('[aria-label*="topic" i], [data-testid*="topic" i]')
        if not box.count():
            print("    (couldn't find the topics field; publishing without topics)")
            return
        box.first.click()
        for tag in tags:
            self.page.keyboard.type(tag, delay=20)
            self.page.keyboard.press("Enter")
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync(items: list[Item], store: StateStore, state: dict, mcfg: dict, args) -> int:
    create, update, _ = plan(items, state, args.force)
    todo = [("create", i) for i in create] + [("update", i) for i in update]
    if len(todo) > args.limit:
        print(f"Doing {args.limit} of {len(todo)} this run (medium.max_per_run); "
              "the rest go next time.")
        todo = todo[:args.limit]
    if not todo:
        return 0

    try:
        cookies = parse_cookies(os.environ.get("MEDIUM_COOKIES", ""))
    except (MediumError, ValueError, KeyError) as err:
        sys.exit(f"MEDIUM_COOKIES: {err}")

    failures = 0
    with MediumSession(cookies, args.headed, args.channel) as medium:
        try:
            medium.check_login()
        except MediumError as err:
            print(f"Screenshot: {medium.screenshot('login')}")
            sys.exit(str(err))
        for n, (op, item) in enumerate(todo):
            if n:
                time.sleep(5)  # go gently
            print(f"{op}: {item.title}  [{item.key}]")
            entry = state["posts"].get(item.key)
            try:
                if entry is None:
                    post_id = medium.new_story(item)
                    # Record the draft at once: if publishing fails, the next run
                    # updates this story instead of creating a second one.
                    entry = state["posts"][item.key] = {"post_id": post_id, "hash": "",
                                                        "title": item.title, "url": ""}
                    store.save(state, f"Medium: draft {item.key}")
                else:
                    medium.edit_story(entry["post_id"], item)
                url = entry.get("url") or ""
                if mcfg["status"] == "public":
                    url = medium.publish(item, first_time=not entry.get("published")) or url
                    entry["published"] = True
                entry.update(hash=item.digest, title=item.title, url=url, synced=now())
                store.save(state, f"Medium: {op} {item.key}")
                print(f"  done: {url or 'https://medium.com/p/' + entry['post_id']}")
            except RuntimeError:
                raise  # the state couldn't be saved: stop before anything is duplicated
            except Exception as err:
                failures += 1
                shot = medium.screenshot(item.key)
                print(f"  FAILED: {err.__class__.__name__}: {err}\n  screenshot: {shot}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=str(HERE / "config.yaml"))
    parser.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    parser.add_argument("--export", metavar="DIR",
                        help="write each story as Medium-ready HTML into DIR; no browser")
    parser.add_argument("--force", action="store_true", help="update every story, changed or not")
    parser.add_argument("--only", metavar="KEY", action="append",
                        help="sync only this note, e.g. essay:on-trust (repeatable)")
    parser.add_argument("--limit", type=int, help="most stories to create/update in one run")
    parser.add_argument("--link", metavar="KEY=STORY_ID", action="append", default=[],
                        help="record an existing Medium story as this note's copy")
    parser.add_argument("--state", metavar="FILE",
                        help="keep the state in this file instead of the git branch")
    parser.add_argument("--headless", action="store_true",
                        help="hide the browser window (Medium's bot check usually blocks this)")
    parser.add_argument("--channel", default=os.environ.get("MEDIUM_BROWSER_CHANNEL"),
                        help="browser to use, e.g. 'chrome' (default: Playwright's Chromium)")
    args = parser.parse_args()
    args.headed = not args.headless

    config_path = Path(args.config).resolve()
    cfg = build.load_config(config_path, local=False)
    mcfg = medium_config(cfg)
    args.limit = args.limit if args.limit is not None else int(mcfg["max_per_run"])

    print("Collecting notes:")
    items = load_items(cfg, config_path.parent, mcfg)
    if args.only:
        unknown = set(args.only) - {i.key for i in items}
        if unknown:
            sys.exit(f"Not on the site: {', '.join(sorted(unknown))}. "
                     f"Keys: {', '.join(i.key for i in items) or '(none)'}")
        items = [i for i in items if i.key in args.only]

    if args.export:
        out = Path(args.export)
        out.mkdir(parents=True, exist_ok=True)
        for item in items:
            name = item.key.replace(":", "-") + ".html"
            (out / name).write_text(f"<!doctype html><meta charset=utf-8>\n{item.story_html}\n",
                                    encoding="utf-8")
        print(f"Wrote {len(items)} stories to {out}")
        return

    root = build.find_repo_root(HERE) or HERE
    store = StateStore(root, None if args.state else mcfg["state_branch"],
                       Path(args.state) if args.state else None)
    state = store.load()

    for pair in args.link:
        key, _, post_id = pair.partition("=")
        if not STORY_ID.search(f"/p/{post_id}/edit"):
            sys.exit(f"--link {pair}: expected KEY=STORY_ID, e.g. essay:on-trust=1a2b3c4d5e6f")
        state["posts"][key] = {"post_id": post_id, "hash": "", "title": "", "url": "",
                               "published": True}
        if not args.dry_run:
            store.save(state, f"Medium: link {key}")
        print(f"Linked {key} to Medium story {post_id}; the next sync updates it.")
    if args.link:
        return  # linking only records; the next sync does the updating

    create, update, same = plan(items, state, args.force)
    print(f"\nMedium: {len(create)} new, {len(update)} changed, {len(same)} unchanged.")
    for label, group in (("new", create), ("changed", update)):
        for item in group:
            print(f"  {label}: {item.title}  [{item.key}]")
    if not args.only:
        for key in gone(items, state):
            print(f"  no longer on the site (left on Medium): {key} "
                  f"{state['posts'][key].get('url', '')}")
    if args.dry_run:
        return

    failures = sync(items, store, state, mcfg, args)
    if failures:
        sys.exit(f"{failures} stor{'y' if failures == 1 else 'ies'} failed; they'll be "
                 "retried on the next run.")


if __name__ == "__main__":
    main()
