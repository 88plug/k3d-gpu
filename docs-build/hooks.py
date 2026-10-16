"""MkDocs hooks for the k3d-gpu docs site.

* README.md is rendered as the home page and CONTRIBUTING.md as a page, so
  each has one source.
* Relative links on every page are read the way GitHub reads them, from the
  source file's place in the repo. A link to a file in docs/ stays a site
  link (``docs/x.md`` in README becomes ``x.md``); a link to any other repo
  path, or to a directory, becomes a GitHub link.
* ``nav`` may list pages that are not in docs/ yet. Those entries are dropped
  with an info message ("not yet published") instead of failing --strict, so
  the nav can be laid out ahead of the branches that add the pages. Links to
  such a page from other pages render as plain text until it lands.
"""

import logging
import os
import posixpath
import re

from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File

log = logging.getLogger("mkdocs.hooks.k3d_gpu")

HOME = "index.md"
# Site page -> repo-root source file.
GENERATED = {HOME: "README.md", "contributing.md": "CONTRIBUTING.md"}
BRANCH = "main"
# Repo directory that docs_dir points at.
DOCS = "docs"

# Nav pages skipped by the last on_config, as docs-relative paths.
_unpublished = set()

_LINK = re.compile(r"(\]\()(?!(?:[a-z][a-z0-9+.-]*:|#|/))([^)\s]+)(\))", re.IGNORECASE)


def prune_nav(items, docs_dir, skipped=None):
    """Return ``items`` without entries whose .md page is missing from docs_dir.

    Sections left empty are dropped too. URLs and generated pages are kept.
    Skipped page paths are added to ``skipped`` when given.
    """
    kept = []
    for item in items:
        title, target = None, item
        if isinstance(item, dict):
            ((title, target),) = item.items()
        if isinstance(target, list):
            children = prune_nav(target, docs_dir, skipped)
            if children:
                kept.append({title: children})
            else:
                log.info("nav: section '%s' has no published pages, skipped", title)
            continue
        if (
            isinstance(target, str)
            and target.endswith(".md")
            and target not in GENERATED
            and "://" not in target
            and not os.path.isfile(os.path.join(docs_dir, target))
        ):
            log.info("nav: %s not yet published, skipped", target)
            if skipped is not None:
                skipped.add(target)
            continue
        kept.append(item)
    return kept


def rewrite_links(markdown, repo_url, src_dir, page_dir):
    """Rewrite relative links for GitHub-style repo paths.

    ``src_dir`` is the repo directory of the source file and ``page_dir`` the
    repo directory the page is served from (docs/ or a subdirectory of it).
    A target inside docs/ is made relative to ``page_dir``; any other target,
    or one ending in ``/``, becomes a GitHub blob or tree URL.
    """
    base = repo_url.rstrip("/") + "/"

    def fix(match):
        target = match.group(2)
        path, hash_, frag = target.partition("#")
        repo_path = posixpath.normpath(posixpath.join(src_dir, path))
        if repo_path.startswith("../"):
            return match.group(0)
        if path.endswith("/"):
            target = base + "tree/" + BRANCH + "/" + repo_path + "/" + hash_ + frag
        elif repo_path.startswith(DOCS + "/"):
            target = posixpath.relpath(repo_path, page_dir) + hash_ + frag
        else:
            target = base + "blob/" + BRANCH + "/" + repo_path + hash_ + frag
        return match.group(1) + target + match.group(3)

    return _LINK.sub(fix, markdown)


def unlink(markdown, pages):
    """Replace inline links to any of ``pages`` with their link text."""
    if not pages:
        return markdown
    alt = "|".join(re.escape(p) for p in sorted(pages))
    return re.sub(r"\[([^\]]+)\]\((?:" + alt + r")(?:#[^)\s]*)?\)", r"\1", markdown)


def on_config(config):
    _unpublished.clear()
    if config.nav:
        config.nav = prune_nav(config.nav, config.docs_dir, _unpublished)
    return config


def on_files(files, config):
    root = os.path.dirname(config.config_file_path)
    for page, source in GENERATED.items():
        if files.get_file_from_path(page) is not None:
            raise PluginError(f"docs/{page} must not exist: it is generated from {source}")
        with open(os.path.join(root, source), encoding="utf-8") as fh:
            files.append(File.generated(config, page, content=fh.read()))
    return files


def on_page_markdown(markdown, page, config, files):
    src_uri = page.file.src_uri
    page_dir = posixpath.join(DOCS, posixpath.dirname(src_uri)).rstrip("/")
    source = GENERATED.get(src_uri)
    if source:
        page.edit_url = config.repo_url.rstrip("/") + "/edit/" + BRANCH + "/" + source
        src_dir = posixpath.dirname(source)
    else:
        src_dir = page_dir
    markdown = rewrite_links(markdown, config.repo_url, src_dir, page_dir)
    # Links to an unpublished page, as written relative to this page.
    linked = {}
    for target in _unpublished:
        rel = posixpath.relpath(posixpath.join(DOCS, target), page_dir)
        if "](" + rel in markdown:
            linked[rel] = target
    for rel in sorted(linked):
        log.info("%s: link to unpublished %s rendered as text", src_uri, linked[rel])
    return unlink(markdown, linked)
