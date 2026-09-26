# Oliver Digests

A minimal personal essay site. You write essays in the Obsidian vault in `vault/`. `build.py` turns the published ones into a static website, and a GitHub Action deploys it to GitHub Pages on every push to `main`.

Each essay keeps a public revision history built from git: a list of versions, each old version as it was, and a word-level diff of what changed.

## Files

| Path | What it is |
|---|---|
| `vault/` | The Obsidian vault. Open this folder as a vault in Obsidian. |
| `vault/essays/` | Where new notes go (any folder in the vault works). |
| `vault/attachments/` | Where Obsidian puts pasted images. |
| `vault/templates/Essay.md` | A note template with every supported property. |
| `config.yaml` | Site title, author, email, address. **The only file you normally edit.** |
| `build.py` | The whole build, in one commented Python file. |
| `templates/` | Jinja2 HTML templates. |
| `static/` | `style.css` and `email.js` (the only JavaScript, and optional). |
| `tests/test_build.py` | End-to-end test in a throwaway git repo. |
| `.github/workflows/deploy.yml` | Build and deploy on push to `main`. |

## One-time setup

### 1. Fill in `config.yaml`

```yaml
title: Oliver Digests
author: Oliver
email: you@example.com        # ← put your real address here
base_url: https://oliver-broadterms.github.io/Oliver-Digests
```

`base_url` must match where the site actually lives:

- **GitHub Pages project site:** `https://<username>.github.io/<repo-name>`. The repo name is case-sensitive and uses hyphens, not spaces. The folder here is "Oliver Digests", so the GitHub repo is most likely `Oliver-Digests`.
- **Custom domain:** `https://example.com`, with no path.

Every link on the site is built from this value, so if it's wrong, CSS and links break.

### 2. Create the GitHub repo and push

This repo has no remote yet. On github.com, create a new repository with no README (e.g. `Oliver-Digests`). Then run:

```sh
git remote add origin https://github.com/<username>/Oliver-Digests.git
git push -u origin main
```

GitHub Pages on a free account requires a **public** repository. Your vault, including unpublished drafts, will then be visible on GitHub even though it isn't on the site. If that matters, keep private notes out of this repo or use a paid plan with a private repo.

### 3. Turn on GitHub Pages

On GitHub, open the repo and go to **Settings → Pages → Build and deployment → Source**, then choose **GitHub Actions**. That's the only setting needed; there is no branch to select.

Then open the **Actions** tab. The "Deploy site" workflow runs on every push; you can re-run it with **Run workflow**. When it finishes, the site is at your `base_url`.

If the first run fails with an environment protection error, go to **Settings → Environments → github-pages** and make sure `main` is allowed to deploy (it is by default).

### 4. (Optional) Custom domain

1. In **Settings → Pages → Custom domain**, enter the domain and save.
2. At your DNS provider, add the records GitHub shows. For an apex domain these are A records to GitHub's IPs; for `www.` it's a CNAME to `<username>.github.io`.
3. Tick **Enforce HTTPS** once it's available.
4. Set `base_url: https://yourdomain.com` in `config.yaml` and push.

### 5. Obsidian

Open the `vault/` folder as a vault. The included settings put new notes in `essays/` and pasted images in `attachments/`, and they update links when you rename notes. To use the template, enable the core **Templates** plugin (its folder is already set to `templates`). Then run "Insert template" on a new note.

## Daily workflow

1. **Write in Obsidian.** A note goes on the site only when its properties include `publish: true`:

   ```yaml
   ---
   title: On Trust            # optional; defaults to the file name
   slug: on-trust             # optional; the URL. Set it once and the URL survives renames
   date: 2026-09-26           # optional; defaults to the date first published
   description: One line for the index page.
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

### What readers see

- **Home page:** all essays, newest first, with date and description.
- **Essay page:** the current text, plus "Email me about this". The email subject is prefilled as `Re: <Title> (v<N>)`. If the reader has selected a passage, it is quoted in the email body.
- **History link:** shown only once an essay has a v2. It leads to a list of versions, each with its date and change note, a link to read that version, and a diff against the previous version.
- **Tags:** one page per tag.

### Writing notes

- `[[Note]]` and `[[Note|text]]` link to the published essay. If that note isn't published, the link text appears as plain text, never as a broken link.
- `![[image.png]]` embeds an image; the file is copied onto the site. `![[image.png|400]]` sets its width.
- Footnotes work: `text[^1]` and then `[^1]: The note.`
- `%% comments %%` are removed. `==highlights==` are shown highlighted.
- A leading `# Heading` that repeats the title is dropped, so the title isn't shown twice.

### Edge cases

- **Renaming or moving a file:** history follows it (`git log --follow`), and the URL stays the same if you set `slug`.
- **Unpublishing:** set `publish: false` and the essay leaves the site on the next push.
  - Commits made while it's unpublished never create versions, even with `rev:`.
  - When you set it back to `true`, the history continues where it left off.
- **Drafts:** commits from before a note was first published are never shown, so v1 is the first *published* commit, not the first draft.
- **Properties-only edits:** frontmatter is never part of a diff. A `rev:` commit that only changes properties creates a version whose diff says the text is unchanged.
- **Branches and merges:** a merge commit whose message starts with `rev:` creates a version, like any other commit.
- **Renaming and heavily rewriting in the same commit:** git may not recognise the rename, and the history would restart at v1. Rename in one commit and rewrite in the next.
- **Reserved slugs:** `tags` and `attachments` are used by the site, so the build stops with a message if an essay would use one.
- **Code:** wikilinks inside fenced code blocks and `inline code` are left alone. Indented (4-space) code blocks aren't detected, so use fences.

## Local preview

```sh
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python build.py --local && python -m http.server 8000 -d _site
```

Then open http://localhost:8000. `--local` builds with links rooted at `/`, so it works with `http.server`. The plain `python build.py` used by the Action builds for `base_url`.

The preview uses the note files as they are on disk, including unsaved-to-git edits, and versions from your committed history.

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

It then builds the site and runs 34 checks, covering:

- **Versions:** v1–v4 exist, with their change notes.
- **Diffs:** the typo fix shows up in the v2 diff, changes are marked word by word with `<del>`/`<ins>`, and no frontmatter appears.
- **Links and embeds:** links to unpublished notes are plain text, images are copied, footnotes render, and table-escaped links work.
- **Emails and tags:** mailto subjects and case-insensitive tags are correct.

It also checks that a repo with no commits yet still builds.

## Customizing

- **Name, email, address:** `config.yaml`. `email_subject` sets the email subject template.
- **Look:** `static/style.css`. The colors are CSS variables at the top, with a separate block for dark mode.
- **Page layout:** `templates/*.html`.
