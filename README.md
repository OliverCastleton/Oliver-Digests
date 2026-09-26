# Oliver Digests

A minimal personal site for essays and book notes. You write in the Obsidian vault in `Oliver Digests Vault/`. `build.py` turns the published notes into a static website, and a GitHub Action deploys it to GitHub Pages on every push to `main`.

- **Essays** keep a public revision history built from git: a list of versions, each old version as it was, and a word-level diff of what changed.
- **Library** lists the books you've read. Each book with notes gets its own page.
- **Homepage** opens with your intro (from `Home.md`), then up to three pinned notes, the latest essays, recently read books and topics.
- **Search** covers every essay and book note. Open it with the Search link, `/` or Ctrl+K.
- **Translate** reads any page in another language.
- **Light/dark theme** follows the reader's system setting, and a button in the header switches it.

## Files

| Path | What it is |
|---|---|
| `Oliver Digests Vault/` | The Obsidian vault. Open this folder as a vault in Obsidian. |
| `…/Home.md` | The intro at the top of the homepage. Always shown; never listed as an essay. |
| `…/essays/` | Where new notes go. Any folder works, except `books/` and `templates/` (never published). |
| `…/books/` | One note per book. These make up the Library. |
| `…/attachments/` | Where Obsidian puts pasted images, including book covers. |
| `…/templates/` | `Essay.md` and `Book.md` note templates. |
| `…/Library.base` | An Obsidian Bases view of your books, as a table and as cover cards. |
| `config.yaml` | Site title, author, email, address. **The only file you normally edit.** |
| `build.py` | The whole build, in one commented Python file. |
| `templates/` | Jinja2 HTML templates. |
| `static/` | `style.css`, the scripts (`theme.js`, `search.js`, `translate.js`, `email.js`) and `fonts/`. |
| `tests/test_build.py` | End-to-end test in a throwaway git repo. |
| `.github/workflows/deploy.yml` | Build and deploy on push to `main`. |

## One-time setup

### 1. Fill in `config.yaml`

```yaml
title: Oliver Digests
author: Oliver
email: you@example.com        # ← put your real address here
base_url: https://olivercastleton.github.io/Oliver-Digests
vault: Oliver Digests Vault
```

`base_url` must match where the site actually lives:

- **GitHub Pages project site:** `https://<username>.github.io/<repo-name>`. The username is lowercase; the repo name is case-sensitive.
- **Custom domain:** `https://example.com`, with no path.

Every link on the site is built from this value, so if it's wrong, CSS and links break. If you rename the vault folder, update `vault:` too.

### 2. Turn on GitHub Pages

The repo is already connected to `github.com/OliverCastleton/Oliver-Digests`. On GitHub, open **Settings → Pages → Build and deployment → Source**, then choose **GitHub Actions**. That's the only setting needed; there is no branch to select.

Then open the **Actions** tab. The "Deploy site" workflow runs on every push; you can re-run it with **Run workflow**. When it finishes, the site is at your `base_url`.

If the first run fails with an environment protection error, go to **Settings → Environments → github-pages** and make sure `main` is allowed to deploy (it is by default).

GitHub Pages on a free account requires a **public** repository. Your vault, including unpublished drafts, will then be visible on GitHub even though it isn't on the site. If that matters, keep private notes out of this repo or use a paid plan with a private repo.

### 3. (Optional) Custom domain

1. In **Settings → Pages → Custom domain**, enter the domain and save.
2. At your DNS provider, add the records GitHub shows. For an apex domain these are A records to GitHub's IPs; for `www.` it's a CNAME to `<username>.github.io`.
3. Tick **Enforce HTTPS** once it's available.
4. Set `base_url: https://yourdomain.com` in `config.yaml` and push.

### 4. Obsidian

Open `Oliver Digests Vault/` as a vault. The included settings put new notes in `essays/` and pasted images in `attachments/`, and they update links when you rename notes.

To start from a template, use the core **Templates** plugin (its folder is set to `templates`): create a note, then run "Insert template" and pick **Essay** or **Book**.

## Example notes

The vault includes published examples, all tagged `example`, so you can see every feature working:

| Note | Shows |
|---|---|
| `essays/A Tour of This Site.md` | Highlights, footnotes, quotes, links to essays and books, a link to an unpublished note (plain text), a private `%% comment %%`, an embedded image, a table. Pinned first. |
| `essays/On Keeping a Commonplace Book.md` | An ordinary essay that links to book notes. Pinned second. |
| `books/Meditations.md` | A book with a cover image, rating and notes. Pinned third. |
| `books/How to Read a Book.md` | A book with notes but no cover image, so it gets a plain cloth binding. |
| `books/The Elements of Style.md` | A book with properties only: listed in the Library, with no page of its own. |

The images are in `attachments/` (`how-it-works.svg`, `meditations-cover.svg`).

Version history only comes from real `rev:` commits, so the examples start with no History link. To see it, edit the tour essay and commit with a message like `rev: Tried out versions`. Its History page and a word-by-word diff will appear.

When you're done with the examples, delete them, or set `publish: false` to keep them for reference.

## Pinning notes to the homepage

Add `pin` to the properties of up to three essays or books:

```yaml
pin: 1        # a number sets the order: 1 first, then 2, then 3
pin: true     # pinned, after any numbered pins, newest first
```

Pinned notes appear in a "Pinned" section right after your intro, and aren't repeated in the lists below it. If more than three are pinned, the build shows the first three and prints a warning naming the rest. Remove `pin` (or set `pin: false`) to unpin. Both templates include an empty `pin:` property.

## Daily workflow: essays

1. **Write in Obsidian.** A note goes on the site only when its properties include `publish: true`:

   ```yaml
   ---
   title: On Trust            # optional; defaults to the file name
   slug: on-trust             # optional; the URL. Set it once and the URL survives renames
   date: 2026-09-26           # optional; defaults to the date first published
   description: One line, shown on the home page and under the title.
   tags: [ethics, society]
   publish: true
   ---
   ```

2. **Commit.** How you word the commit message decides whether it creates a new version:

   | Commit message | Effect |
   |---|---|
   | anything (first commit where the note has `publish: true`) | becomes **v1** |
   | `rev: Rewrote the conclusion` | new version; "Rewrote the conclusion" is its change note |
   | anything else (`fix typo`, `wip`) | updates the live page only, with no new version |

   Changes from non-`rev:` commits aren't lost. They show up in the next `rev:` version's diff.

   ```sh
   git add -A
   git commit -m "rev: Expanded the section on institutions"
   git push
   ```

   The Obsidian Git plugin can do the same from inside Obsidian. Just write the commit message yourself when a change should count as a version.

3. **Push.** The Action rebuilds and deploys in about a minute.

## The Library: book notes

Every note in `books/` that has `publish: true` is a book in the Library. Use the **Book** template, or write the properties yourself:

```yaml
---
title: The Idiot              # optional; defaults to the file name
author: Fyodor Dostoevsky
published: 1869               # year the book came out
finished: 2026-03-02          # when you finished it; the Library is grouped by this year
rating: 5                     # 1–5, shown as stars
cover: "[[the-idiot.jpg]]"    # an image in the vault (or a plain file name or https URL)
description: One line about the book.
tags: [novels]
publish: true                 # the Book template starts with true
---

Your notes go here: quotes, thoughts, a review.
```

- **Pages:** the Library lists every published book, most recently finished first. A book whose note has a body gets its own notes page, linked from the list. A book with only properties is listed without a page.
- **Covers:** put the image in `attachments/` (drag it into Obsidian) and set `cover` to its name. With no cover, a simple placeholder is shown.
- **Links:** `[[The Idiot]]` in an essay links to the book's notes page. Book tags show up on the tag pages alongside essays.
- **No history:** books don't have versions or history pages. Commit them however you like.
- **In Obsidian:** open `Library.base` to see your books as a sortable table, or as cover cards. This needs the core **Bases** plugin, which is already on.

## What readers see

- **Homepage:** your intro from `Home.md`, the five latest essays, a shelf of recently read books, and your topics.
- **Essays page:** every essay, grouped by year, newest first.
- **Essay page:** the current text, plus "Email me about this". The email subject is prefilled as `Re: <Title> (v<N>)`. If the reader has selected a passage, it is quoted in the email body.
- **History link:** shown only once an essay has a v2. It leads to a list of versions, each with its date and change note, a link to read that version, and a diff against the previous version.
- **Library:** books grouped by the year you finished them, with cover, author and rating, and a notes page per book with notes.
- **Topics:** one page per tag, listing both essays and books.
- **Search:** a search window over every essay and book note. Words match the start of words, and accents and capitals are ignored. Arrow keys move through the results and Enter opens one. Without JavaScript the Search link opens a page pointing to the essay, library and topic lists instead.
- **Translate button (文A) in the header:** a menu of languages.
  - **Built-in translation:** browsers that can translate on the device (recent Chrome and Edge on desktop) translate the page in place, and nothing is sent anywhere. The language stays on as the reader moves between pages until they click "Show original".
  - **Google Translate:** other browsers open the page there. This only works once the site is online.
  - **Choosing languages:** edit `translate_languages` in `config.yaml`.
- **Theme button (moon/sun) in the header:** switches between light and dark. The choice is remembered on that reader's browser. Without JavaScript the button is hidden and the site follows the system setting.

## Writing notes

- `[[Note]]` and `[[Note|text]]` link to the published essay or book. If that note isn't published, the link text appears as plain text, never as a broken link.
- `![[image.png]]` embeds an image; the file is copied onto the site. `![[image.png|400]]` sets its width.
- Footnotes work: `text[^1]` and then `[^1]: The note.`
- `%% comments %%` are removed. `==highlights==` are shown highlighted.
- A leading `# Heading` that repeats the title is dropped, so the title isn't shown twice.

## Edge cases

- **Renaming or moving a file:** history follows it (`git log --follow`), and the URL stays the same if you set `slug`.
- **Unpublishing:** set `publish: false` and the essay leaves the site on the next push.
  - Commits made while it's unpublished never create versions, even with `rev:`.
  - When you set it back to `true`, the history continues where it left off.
- **Drafts:** commits from before a note was first published are never shown, so v1 is the first *published* commit, not the first draft.
- **Properties-only edits:** frontmatter is never part of a diff. A `rev:` commit that only changes properties creates a version whose diff says the text is unchanged.
- **Branches and merges:** a merge commit whose message starts with `rev:` creates a version, like any other commit.
- **Renaming and heavily rewriting in the same commit:** git may not recognise the rename, and the history would restart at v1. Rename in one commit and rewrite in the next.
- **Reserved slugs:** `essays`, `library`, `tags`, `search` and `attachments` are used by the site, so the build stops with a message if an essay would use one.
- **Code:** wikilinks inside fenced code blocks and `inline code` are left alone. Indented (4-space) code blocks aren't detected, so use fences.

## Local preview

```sh
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python build.py --local && python -m http.server 8000 -d _site
```

Then open http://localhost:8000. `--local` builds with links rooted at `/`, so it works with `http.server`. The plain `python build.py` used by the Action builds for `base_url`.

The preview uses the note files as they are on disk, including edits you haven't committed, and versions from your committed history.

## Tests

```sh
python tests/test_build.py
```

This creates a throwaway git repo in a temp folder and never touches this repo. The repo has a sample essay that goes through these commits:

1. a draft
2. the first published version
3. a typo fix
4. a `rev:` commit
5. a rename
6. unpublish and republish
7. another `rev:` commit
8. a branch merged back in with a `rev:` merge commit

The repo also has three books: one with notes and a cover, one without notes, and one unpublished.

It then builds the site and runs 61 checks, covering:

- **Versions:** v1–v4 exist, with their change notes.
- **Diffs:** the typo fix shows up in the v2 diff, changes are marked word by word with `<del>`/`<ins>`, and no frontmatter appears.
- **Links and embeds:** links to unpublished notes are plain text, images are copied, footnotes render, and table-escaped links work.
- **Emails and tags:** mailto subjects and case-insensitive tags are correct.
- **Library:** ordering and year groups, covers, star ratings, book pages only for books with notes, and links from essays to books.
- **Homepage:** the intro from `Home.md`, and recent essays, books and topics.
- **Pins:** numbered pins come first, only three are shown (with a warning for the rest), and pinned notes aren't repeated.
- **Search:** the index holds plain text, links books without notes to the library, and leaves out unpublished notes.
- **Translate, theme toggle and fonts:** all present, with translate links pointing at each page.

It also checks that a repo with no commits yet still builds.

## Design and customizing

The look is a notebook with notes in the margin:

- **Type:** one typeface, [Newsreader](https://github.com/productiontype/Newsreader). It's upright for reading and italic for everything beside the text: dates, versions, years and section names. On wide screens these sit in a margin column to the left of the text; on phones they stack above it. The font is self-hosted in `static/fonts/` under the SIL Open Font License (license included), so the site makes no requests to other servers.
- **Colour:** blue-black ink on cool paper. The one bright colour is highlighter yellow. It shows when you point at a link, when you select text, on search matches, for `==highlights==`, and under the current page in the navigation.
- **Motion:** only in response to the reader. Pages crossfade, an essay title glides from a list into its page, and a book cover glides into its notes page. The theme spreads out from its button, and menus open smoothly. Readers who have asked their system for reduced motion get none of it.

To change things:

- **Name, email, address:** `config.yaml`. `email_subject` and `book_email_subject` set the email subject lines, and `translate_languages` sets the Translate menu.
- **Homepage intro:** edit `Home.md` in the vault.
- **Colours:** CSS variables at the top of `static/style.css`: one light block, and a dark block that appears twice (once for the system setting, once for the toggle). Keep the two dark blocks identical.
- **Page layout:** `templates/*.html`. Shared list items and covers are in `templates/_macros.html`.
