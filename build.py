#!/usr/bin/env python3
"""
build.py: turn the Obsidian vault into a static essay website.

Usage
    python build.py                 # production build, uses base_url from config.yaml
    python build.py --local         # build for preview at http://localhost:8000/
    python build.py --config other/config.yaml

What happens, in order
    1. Load config.yaml.
    2. Find every note in the vault with `publish: true` in its frontmatter.
    3. For each one, read its git history and work out its versions:
         - the first commit in which the note is published is v1;
         - after that, only commits whose message starts with `rev:` add a version.
    4. Render Markdown to HTML (wikilinks, image embeds, footnotes).
    5. Compute word-level diffs between consecutive versions.
    6. Write every page into the output folder using the Jinja templates.

Everything is computed here, at build time. The site needs no server code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin

# templates/ and static/ always live next to this script.
HERE = Path(__file__).resolve().parent

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".bmp"}

# Commits whose message starts with this (case-insensitive) create a new version.
REV_PREFIX = re.compile(r"^\s*rev:\s*", re.IGNORECASE)

# Note used for v1 when its commit message isn't a `rev:` message.
FIRST_VERSION_NOTE = "First published."


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path, local: bool) -> dict:
    """Read config.yaml, fill in defaults, and work out the base path.

    The base path is the part of base_url after the domain. It is
    "/Oliver-Digests" for https://user.github.io/Oliver-Digests and "" for a
    custom domain. Every internal link is prefixed with it.
    """
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("title", "author", "email", "base_url"):
        if not cfg.get(key):
            sys.exit(f"config.yaml: '{key}' is required")

    cfg.setdefault("description", "")
    cfg.setdefault("language", "en")
    cfg.setdefault("vault", "vault")
    cfg.setdefault("output", "_site")
    cfg.setdefault("email_subject", "Re: {title} (v{version})")

    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")
    if local:
        # `python -m http.server` serves the output folder at the root.
        cfg["base_url"] = "http://localhost:8000"
    cfg["base_path"] = urlparse(cfg["base_url"]).path.rstrip("/")
    return cfg


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

# Frontmatter is a YAML block between two `---` lines at the very top.
FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?", re.DOTALL | re.MULTILINE)

# Obsidian comments (%% like this %%) are private and never published.
OBSIDIAN_COMMENT = re.compile(r"%%.*?%%", re.DOTALL)


def split_frontmatter(text: str, source: object = "") -> tuple[dict, str]:
    """Return (frontmatter dict, body without frontmatter)."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as err:
        print(f"  warning: unreadable frontmatter ignored in {source} ({err.__class__.__name__})")
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[match.end():]


def clean_body(body: str, title: str) -> str:
    """Remove private comments and a leading `# Title` heading that repeats the title."""
    body = OBSIDIAN_COMMENT.sub("", body).strip()
    first_line, _, rest = body.partition("\n")
    if first_line.startswith("# ") and first_line[2:].strip().lower() == title.lower():
        body = rest.strip()
    return body


def is_published(meta: dict) -> bool:
    value = meta.get("publish")
    return value is True or str(value).strip().lower() == "true"


def slugify(text: str) -> str:
    """'Über Trust, Part 2' -> 'uber-trust-part-2'."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "untitled"


def to_date(value) -> dt.date | None:
    """Accept a YAML date, datetime, or 'YYYY-MM-DD...' string."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def parse_tags(value) -> list[str]:
    """Tags may be a YAML list or a comma/space separated string; '#' is optional."""
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        value = re.split(r"[,\s]+", str(value))
    tags = {str(t).strip().lstrip("#") for t in value}
    return sorted((t for t in tags if t), key=str.lower)


def tag_slug(tag: str) -> str:
    """'Society' and 'society' share a page; non-Latin tags like '日本' keep their letters."""
    return re.sub(r"\W+", "-", tag.casefold()).strip("-_") or "tag"


def format_date(d: dt.date) -> str:
    """26 September 2026"""
    return f"{d.day} {d:%B %Y}"


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

class GitError(Exception):
    pass


def git(root: Path, *args: str) -> str:
    # core.quotepath=off keeps non-ASCII file names readable in the output.
    result = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True
    )
    if result.returncode != 0:
        raise GitError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout.decode("utf-8", "replace")


def find_repo_root(start: Path) -> Path | None:
    try:
        root = Path(git(start, "rev-parse", "--show-toplevel").strip())
    except (GitError, FileNotFoundError):
        return None
    if git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        print("WARNING: this is a shallow clone, so version history will be incomplete.\n"
              "         In GitHub Actions, use actions/checkout with `fetch-depth: 0`.")
    return root


@dataclass
class Commit:
    sha: str
    date: dt.datetime
    message: str
    path: str  # the note's path *in this commit*, which changes when it is renamed


def file_history(root: Path, relpath: str) -> list[Commit]:
    """Every commit that touched this file, oldest first, following renames.

    Each record is printed as: \x1e sha \x1f date \x1f message \x1d file-name.
    The control characters can't appear in commit messages, so parsing is safe.
    """
    # --diff-merges=first-parent makes merge commits list the files they
    # changed, so a merge commit titled `rev: ...` can create a version.
    try:
        out = git(root, "log", "--follow", "--name-only", "--diff-merges=first-parent",
                  "--format=%x1e%H%x1f%aI%x1f%B%x1d", "--", relpath)
    except GitError:
        return []  # e.g. a brand-new repo with no commits yet
    commits = []
    for record in out.split("\x1e")[1:]:
        header, _, names = record.partition("\x1d")
        sha, date, message = header.split("\x1f", 2)
        paths = names.strip().splitlines()
        if not paths:
            continue  # the commit didn't change this file
        commits.append(Commit(sha, dt.datetime.fromisoformat(date), message.strip(), paths[0]))
    commits.reverse()
    return commits


def read_at(root: Path, commit: Commit) -> str | None:
    """The file's content at a commit, or None if it doesn't exist there (e.g. deleted)."""
    try:
        return git(root, "show", f"{commit.sha}:{commit.path}")
    except GitError:
        return None


# ---------------------------------------------------------------------------
# Essays and versions
# ---------------------------------------------------------------------------

@dataclass
class Version:
    number: int
    date: dt.date
    note: str
    title: str
    body: str  # Markdown, frontmatter and comments removed


@dataclass
class Essay:
    source: Path
    name: str            # file name without .md, which is what [[wikilinks]] use
    title: str
    slug: str
    url: str             # e.g. /Oliver-Digests/on-trust/
    date: dt.date
    description: str
    tags: list[str]
    body: str            # current content from the working tree (= HEAD in CI)
    versions: list[Version] = field(default_factory=list)

    @property
    def current(self) -> Version:
        return self.versions[-1]

    @property
    def has_history(self) -> bool:
        return len(self.versions) > 1


def compute_versions(root: Path | None, relpath: str, fallback_title: str) -> list[Version]:
    """Walk the note's git history and pick out the commits that count as versions.

    Rules:
      - Only commits where the note exists *and* has `publish: true` are considered,
        so drafts written before publishing, or while unpublished, are never exposed.
      - The first such commit is v1.
      - After that, a commit is a new version only if its message starts with `rev:`.
        Other commits (typo fixes) are skipped here; their edits appear in the
        next `rev:` version because each version is a full snapshot.
    """
    if root is None:
        return []
    versions: list[Version] = []
    for commit in file_history(root, relpath):
        text = read_at(root, commit)
        if text is None:
            continue
        meta, body = split_frontmatter(text)
        if not is_published(meta):
            continue
        is_rev = bool(REV_PREFIX.match(commit.message))
        if versions and not is_rev:
            continue
        note = REV_PREFIX.sub("", commit.message, count=1).strip() if is_rev else ""
        title = str(meta.get("title") or fallback_title)
        versions.append(Version(
            number=len(versions) + 1,
            date=commit.date.date(),
            note=note or FIRST_VERSION_NOTE,
            title=title,
            body=clean_body(body, title),
        ))
    return versions


def load_essays(cfg: dict, vault: Path, root: Path | None) -> list[Essay]:
    """Find every published note and build its Essay, including versions."""
    essays: list[Essay] = []
    for path in sorted(vault.rglob("*.md")):
        if ".obsidian" in path.relative_to(vault).parts:
            continue
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"), path.relative_to(vault))
        if not is_published(meta):
            continue

        title = str(meta.get("title") or path.stem)
        slug = slugify(meta.get("slug") or path.stem)
        print(f"  {path.relative_to(vault)} -> /{slug}/")

        in_repo = root is not None and path.is_relative_to(root)
        relpath = path.relative_to(root).as_posix() if in_repo else ""
        versions = compute_versions(root if in_repo else None, relpath, title)
        if not versions:
            # Not committed yet (or no git): treat the file on disk as v1.
            versions = [Version(1, dt.date.today(), FIRST_VERSION_NOTE, title,
                                clean_body(body, title))]

        essays.append(Essay(
            source=path,
            name=path.stem,
            title=title,
            slug=slug,
            url=f"{cfg['base_path']}/{slug}/",
            date=to_date(meta.get("date")) or versions[0].date,
            description=str(meta.get("description") or ""),
            tags=parse_tags(meta.get("tags")),
            body=clean_body(body, title),
            versions=versions,
        ))

    # Two notes with the same slug would overwrite each other, so stop.
    reserved = {"tags", "attachments", "404.html", "index.html", ".nojekyll"}
    reserved |= {p.name for p in (HERE / "static").iterdir()}
    seen: dict[str, Path] = {}
    for essay in essays:
        if essay.slug in reserved:
            sys.exit(f"{essay.source}: slug '{essay.slug}' is reserved by the site. "
                     "Set a different `slug:` in its properties.")
        if essay.slug in seen:
            sys.exit(f"Slug '{essay.slug}' is used by both {seen[essay.slug]} and "
                     f"{essay.source}. Set a different `slug:` in one of them.")
        seen[essay.slug] = essay.source

    essays.sort(key=lambda e: (e.date, e.title), reverse=True)  # newest first
    return essays


# ---------------------------------------------------------------------------
# Markdown: wikilinks, embeds, footnotes
# ---------------------------------------------------------------------------

# ![[target|alias]] or [[target#heading|alias]]
# Inside tables Obsidian writes the pipe as "\|", so a backslash may precede it.
WIKILINK = re.compile(r"(!?)\[\[([^\[\]|#\\]*)(#[^\[\]|\\]*)?(?:\\?\|([^\[\]]*))?\]\]")

# Code that must be left untouched: fenced blocks (closed by a fence at least
# as long as the opening one) and inline code spans. Indented code blocks are
# not detected, because they look like indented list items; use fences.
CODE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,}).*?(?:^ {0,3}(?P=fence)[`~]*[ \t]*$|\Z)"
    r"|(?P<tick>`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)(?P=tick)(?!`)",  # not across paragraphs
    re.DOTALL | re.MULTILINE)

HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==")


def outside_code(text: str, fn) -> str:
    """Apply fn to every part of text that isn't code."""
    out, last = [], 0
    for m in CODE.finditer(text):
        out += [fn(text[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def md_escape(text: str) -> str:
    """Backslash-escape all ASCII punctuation so text is never read as Markdown or HTML."""
    return re.sub(r"([!-/:-@\[-`{-~])", r"\\\1", text)


class Linker:
    """Resolves Obsidian wikilinks and embeds against the set of published essays."""

    def __init__(self, essays: list[Essay], vault: Path, out: Path, base_path: str):
        self.base_path = base_path
        self.out = out
        # Look notes up by file name, case-insensitive, as Obsidian does.
        self.essays = {e.name.lower(): e for e in essays}
        # All files in the vault, for ![[embeds]], looked up by file name.
        self.files: dict[str, Path] = {}
        for p in sorted(vault.rglob("*")):
            if p.is_file() and ".obsidian" not in p.relative_to(vault).parts:
                self.files.setdefault(p.name.lower(), p)
        self.copied: set[str] = set()
        # Links pointing at notes that aren't published.
        self.unresolved: set[str] = set()

    def _essay_for(self, target: str) -> Essay | None:
        name = target.strip().rsplit("/", 1)[-1]
        if name.lower().endswith(".md"):
            name = name[:-3]
        return self.essays.get(name.lower())

    def _embed(self, target: str, alias: str | None) -> str:
        """![[image.png]]: copy the file into the output and return an image/link."""
        file = self.files.get(target.strip().rsplit("/", 1)[-1].lower())
        if file is None or file.suffix.lower() == ".md":
            # Embedding a note: we don't transclude, we just link it.
            return self._link(target, alias)
        if file.name not in self.copied:
            (self.out / "attachments").mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, self.out / "attachments" / file.name)
            self.copied.add(file.name)
        url = f"{self.base_path}/attachments/{quote(file.name)}"
        # In Obsidian, ![[img.png|300]] sets a width; any other alias is alt text.
        if file.suffix.lower() in IMAGE_EXTENSIONS:
            if alias and re.fullmatch(r"\d+(x\d+)?", alias.strip()):
                width = alias.strip().split("x")[0]
                return f'<img src="{url}" alt="" width="{width}" loading="lazy">'
            return f"![{md_escape(alias or '')}]({url})"
        return f"[{md_escape(alias or file.name)}]({url})"

    def _link(self, target: str, alias: str | None, heading: str = "") -> str:
        """[[Note|text]]: a link if Note is published, otherwise plain text."""
        text = (alias or target).strip()
        if not target.strip():  # [[#Heading]] points inside the same note
            return md_escape(text or heading.lstrip("#").strip())
        essay = self._essay_for(target)
        if essay is None:
            self.unresolved.add(target.strip())
            return md_escape(text)
        return f"[{md_escape(text)}]({essay.url})"

    def replace(self, markdown: str) -> str:
        def sub(m: re.Match) -> str:
            bang, target, heading, alias = m.groups()
            if bang and target.strip():
                return self._embed(target, alias)
            return self._link(target, alias, heading or "")
        return outside_code(markdown, lambda part: WIKILINK.sub(sub, part))


def make_markdown() -> MarkdownIt:
    """CommonMark + tables, strikethrough, smart quotes and footnotes."""
    return (
        MarkdownIt("commonmark", {"typographer": True, "html": True})
        .enable(["table", "strikethrough", "replacements", "smartquotes"])
        .use(footnote_plugin)
    )


def render_markdown(md: MarkdownIt, linker: Linker, body: str) -> str:
    body = linker.replace(body)
    body = outside_code(body, lambda p: HIGHLIGHT.sub(r"<mark>\1</mark>", p))
    return md.render(body)


# ---------------------------------------------------------------------------
# Diffs
# ---------------------------------------------------------------------------
# Diffs compare Markdown *source* text, frontmatter excluded, paragraph by
# paragraph. Matching paragraphs are shown unchanged. Edited paragraphs get
# a word-level diff. Added or removed paragraphs are shown whole. The output
# is escaped plain text wrapped in <del>/<ins>, so Markdown syntax in a
# change can never break the page.

# Words, runs of punctuation, and whitespace are separate tokens, so a typo
# fix highlights only the word that changed.
TOKEN = re.compile(r"\w+|[^\w\s]+|\s+")

# Paragraphs this similar (0 to 1) count as the same paragraph, edited.
SIMILARITY = 0.4


def paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def readable_source(body: str) -> str:
    """Show [[Note|text]] as just 'text' in diffs; the brackets are noise."""
    return WIKILINK.sub(lambda m: (m.group(4) or m.group(2)).strip()
                        if not m.group(1) else m.group(0), body)


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.split(), b.split(), autojunk=False).ratio()


def diff_words(old: str, new: str) -> tuple[str, int, int]:
    """Word-level diff of two paragraphs -> (html, words removed, words added)."""
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    out, removed, added = [], 0, 0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
            continue
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
            removed += sum(1 for t in a[i1:i2] if not t.isspace())
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
            added += sum(1 for t in b[j1:j2] if not t.isspace())
    return "".join(out), removed, added


def word_count(text: str) -> int:
    return sum(1 for t in TOKEN.findall(text) if not t.isspace())


def diff_bodies(old_body: str, new_body: str) -> dict:
    """Diff two versions -> {'blocks': [{'kind', 'html'}], 'removed': n, 'added': n}.

    kind is 'same', 'changed', 'added' or 'removed'.
    """
    a = paragraphs(readable_source(old_body))
    b = paragraphs(readable_source(new_body))
    blocks: list[dict] = []
    stats = {"removed": 0, "added": 0}

    def same(p):
        blocks.append({"kind": "same", "html": html.escape(p)})

    def removed(p):
        blocks.append({"kind": "removed", "html": f"<del>{html.escape(p)}</del>"})
        stats["removed"] += word_count(p)

    def added(p):
        blocks.append({"kind": "added", "html": f"<ins>{html.escape(p)}</ins>"})
        stats["added"] += word_count(p)

    def changed(old, new):
        text, r, a_ = diff_words(old, new)
        blocks.append({"kind": "changed", "html": text})
        stats["removed"] += r
        stats["added"] += a_

    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for p in a[i1:i2]:
                same(p)
        elif op == "delete":
            for p in a[i1:i2]:
                removed(p)
        elif op == "insert":
            for p in b[j1:j2]:
                added(p)
        else:
            # A block of old paragraphs was replaced by a block of new ones.
            # Pair each new paragraph with the most similar remaining old one
            # (keeping order). Anything left unpaired was added or removed.
            old, i = a[i1:i2], 0
            for new in b[j1:j2]:
                scores = [(similarity(old[k], new), k) for k in range(i, len(old))]
                score, best = max(scores, key=lambda s: (s[0], -s[1]), default=(0.0, -1))
                if score >= SIMILARITY:
                    for p in old[i:best]:
                        removed(p)
                    changed(old[best], new)
                    i = best + 1
                else:
                    added(new)
            for p in old[i:]:
                removed(p)

    return {"blocks": blocks, **stats}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def mailto(cfg: dict, title: str, version: int) -> str:
    subject = cfg["email_subject"].format(title=title, version=version)
    return f"mailto:{cfg['email']}?subject={quote(subject, safe='')}"


class Site:
    def __init__(self, cfg: dict, out: Path):
        self.cfg = cfg
        self.out = out
        self.env = Environment(
            loader=FileSystemLoader(HERE / "templates"),
            autoescape=True,
            undefined=StrictUndefined,  # a typo in a template fails loudly
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["date"] = format_date
        self.env.filters["tag_slug"] = tag_slug
        # Available in every template.
        self.env.globals.update(site=cfg, base=cfg["base_path"], year=dt.date.today().year)

    def write(self, relpath: str, template: str, **context) -> None:
        """Render a template to out/relpath."""
        path = self.out / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        # Canonical URL of this page: base_url plus relpath without "index.html".
        page = relpath.removesuffix("index.html")
        html_text = self.env.get_template(template).render(
            canonical=f"{self.cfg['base_url']}/{page}", **context)
        path.write_text(html_text, encoding="utf-8")


def build(cfg: dict, config_dir: Path) -> None:
    vault = (config_dir / cfg["vault"]).resolve()
    out = (config_dir / cfg["output"]).resolve()
    if not vault.is_dir():
        sys.exit(f"Vault folder not found: {vault}")
    # Safety: never delete the project or the vault by mistake.
    if out in (config_dir, vault) or vault.is_relative_to(out):
        sys.exit(f"Refusing to use {out} as the output folder.")

    print(f"Building {cfg['title']} -> {out}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    root = find_repo_root(vault)  # the vault may even be its own git repo
    essays = load_essays(cfg, vault, root)
    linker = Linker(essays, vault, out, cfg["base_path"])
    md = make_markdown()
    site = Site(cfg, out)

    for essay in essays:
        render = lambda body: render_markdown(md, linker, body)  # noqa: E731
        cur = essay.current

        # The live page: current content, labelled with the latest version number.
        site.write(f"{essay.slug}/index.html", "essay.html",
                   essay=essay, version=cur, is_current=True,
                   title=essay.title, content=render(essay.body),
                   mailto=mailto(cfg, essay.title, cur.number))

        if not essay.has_history:
            continue  # a single version gets no history, version or diff pages

        site.write(f"{essay.slug}/history/index.html", "history.html", essay=essay)

        for v in essay.versions:
            site.write(f"{essay.slug}/v/{v.number}/index.html", "essay.html",
                       essay=essay, version=v, is_current=False,
                       title=v.title, content=render(v.body),
                       mailto=mailto(cfg, v.title, v.number))

        for prev, v in zip(essay.versions, essay.versions[1:]):
            site.write(f"{essay.slug}/diff/{v.number}/index.html", "diff.html",
                       essay=essay, version=v, previous=prev,
                       diff=diff_bodies(prev.body, v.body))

    # Tags: one page per tag, plus an overview.
    # Grouped by slug, so "AI" and "ai" share one page, named by the first spelling seen.
    names: dict[str, str] = {}
    tags: dict[str, list[Essay]] = {}
    for essay in essays:
        for tag in essay.tags:
            name = names.setdefault(tag_slug(tag), tag)
            tags.setdefault(name, []).append(essay)
    tag_list = sorted(tags.items(), key=lambda kv: kv[0].casefold())
    for tag, tagged in tag_list:
        site.write(f"tags/{tag_slug(tag)}/index.html", "tag.html", tag=tag, essays=tagged)
    site.write("tags/index.html", "tags.html", tags=tag_list)

    site.write("index.html", "index.html", essays=essays)
    site.write("404.html", "404.html")

    for asset in (HERE / "static").iterdir():
        shutil.copy2(asset, out / asset.name)
    (out / ".nojekyll").touch()

    if linker.unresolved:
        print("  links shown as plain text (note not published): "
              + ", ".join(sorted(linker.unresolved)))
    print(f"Done: {len(essays)} essays, {len(tags)} tags.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=str(HERE / "config.yaml"),
                        help="path to config.yaml (paths in it are relative to it)")
    parser.add_argument("--local", action="store_true",
                        help="build for local preview at http://localhost:8000/")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    build(load_config(config_path, args.local), config_path.parent)


if __name__ == "__main__":
    main()
