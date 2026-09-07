"""Stage readable Markdown with links matching each release layout."""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.helpers import parseLinkDestination


MARKDOWN = MarkdownIt("commonmark")
DESTINATION_START = re.compile(r"\]\(\s*|^ {0,3}\[[^\]\n]+\]:\s*", re.MULTILINE)
HTML_URL = re.compile(r"""\b(?:href|src)\s*=\s*(["'])(.*?)\1""", re.IGNORECASE)


class HtmlLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(value for key, value in attrs
                          if key in ("href", "src") and value)


def document_links(text):
    """Read actual link destinations, excluding fenced and inline code."""
    links = []
    tokens = MARKDOWN.parse(text)
    for token in tokens:
        for child in token.children or []:
            if child.type in ("link_open", "image"):
                links.append(child.attrGet("href") or child.attrGet("src"))
            if child.type == "html_inline":
                parser = HtmlLinks()
                parser.feed(child.content)
                links.extend(parser.links)
        if token.type == "html_block":
            parser = HtmlLinks()
            parser.feed(token.content)
            links.extend(parser.links)
    return links


def local_target(source, url):
    parts = urlsplit(unescape(url))
    if parts.scheme or parts.netloc or not parts.path:
        return None
    if parts.path.startswith("/"):
        return None
    return posixpath.normpath(posixpath.join(
        posixpath.dirname(source), unquote(parts.path)))


def rewrite_links(text, source, target, file_map, repository_url):
    known = {MARKDOWN.normalizeLink(url) for url in document_links(text)}
    protected = set()
    for token in MARKDOWN.parse(text):
        if token.type in ("fence", "code_block") and token.map:
            protected.update(range(*token.map))

    def relocate(url):
        original = unescape(url)
        if MARKDOWN.normalizeLink(original) not in known:
            return url
        resolved = local_target(source, original)
        if resolved is None:
            return url
        parts = urlsplit(original)
        suffix = ("?" + parts.query if parts.query else "") + (
            "#" + parts.fragment if parts.fragment else "")
        if resolved in file_map:
            relative = posixpath.relpath(file_map[resolved],
                                        posixpath.dirname(target) or ".")
            return quote(relative, safe="/._-") + suffix
        return repository_url + quote(resolved, safe="/._-") + suffix

    def rewrite_chunk(chunk):
        edits = []
        code_spans = [match.span() for match in re.finditer(
            r"(`+)(?!`).*?\1(?!`)", chunk)]
        def inside_code(position):
            return any(start <= position < end for start, end in code_spans)
        for match in DESTINATION_START.finditer(chunk):
            if inside_code(match.start()):
                continue
            parsed = parseLinkDestination(chunk, match.end(), len(chunk))
            if parsed.ok:
                start, end = match.end(), parsed.pos
                raw = chunk[start:end]
                replacement = relocate(parsed.str)
                if replacement != parsed.str:
                    if raw.startswith("<"):
                        replacement = "<" + replacement + ">"
                    edits.append((start, end, replacement))
        for match in HTML_URL.finditer(chunk):
            if inside_code(match.start()):
                continue
            replacement = relocate(match.group(2))
            if replacement != match.group(2):
                edits.append((match.start(2), match.end(2), replacement))
        for start, end, replacement in sorted(edits, reverse=True):
            chunk = chunk[:start] + replacement + chunk[end:]
        return chunk

    lines = text.splitlines(keepends=True)
    return "".join(line if index in protected else rewrite_chunk(line)
                   for index, line in enumerate(lines))


def stage_documentation(root: Path, destination: Path, version: str, *, edge=False):
    files = {}
    if edge:
        sources = list((root / "edge_side_pkg").rglob("*.md"))
        sources += [root / "docs/EDGE_DEVICE_INTERFACES.md", root / "LICENSE"]
    else:
        sources = [root / name for name in
                   ("README.md", "CHANGELOG.md", "LICENSE", "需求分析.md")]
        sources += list((root / "docs").rglob("*.md"))
        sources += list((root / "edge_side_pkg").rglob("*.md"))
    for path in sources:
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") or part in ("build", "devel", "install", "log")
               for part in path.relative_to(root).parts):
            continue
        target = relative
        if not edge and relative.startswith("edge_side_pkg/"):
            target = "docs/edge/" + relative[len("edge_side_pkg/"):]
        files[relative] = target

    file_map = dict(files)
    for path in destination.rglob("*"):
        if path.is_file():
            relative = path.relative_to(destination).as_posix()
            file_map.setdefault(relative, relative)
            if relative.startswith("_internal/"):
                file_map.setdefault(relative[len("_internal/"):], relative)
    repository_url = f"https://github.com/AADCL/CCS_dev/blob/pre-release-v{version}/"
    for source, target in files.items():
        output = destination / target
        output.parent.mkdir(parents=True, exist_ok=True)
        text = (root / source).read_text(encoding="utf-8")
        output.write_text(rewrite_links(text, source, target, file_map,
                                       repository_url), encoding="utf-8")
