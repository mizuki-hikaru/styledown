import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Protocol

from mistletoe import Document
from mistletoe.block_token import BlockToken, Heading, Paragraph, tokenize
from mistletoe.html_renderer import HtmlRenderer
from mistletoe.span_token import tokenize_inner

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

@dataclass(frozen=True)
class Breadcrumb:
    href: str
    title: str

@dataclass(frozen=True)
class IndexEntry:
    href: str
    title: str
    description: str

@dataclass(frozen=True)
class Metadata:
    title: str
    description: str

class _PeekableLines(Protocol):
    def __next__(self) -> str: ...
    def peek(self, n: int = 1) -> Optional[str]: ...

class Div(BlockToken):
    pattern = re.compile(r"^ {0,3}((?:\.[A-Za-z][A-Za-z0-9_-]*)+):(.*)$")

    @classmethod
    def start(cls, line: Optional[str]) -> bool:
        if line is None:
            return False
        return cls.pattern.match(line) is not None

    @classmethod
    def check_interrupts_paragraph(cls, lines: _PeekableLines) -> bool:
        return cls.start(lines.peek())

    @classmethod
    def read(cls, lines: _PeekableLines) -> tuple[str, list[str]]:
        first = next(lines)
        match = cls.pattern.match(first)

        class_name = " ".join(match.group(1).split(".")).strip()
        rest = match.group(2).strip()

        if rest:
            return class_name, [rest + "\n"]

        child_lines = []

        while lines.peek() is not None:
            line = lines.peek()

            if line.strip() == "":
                child_lines.append(next(lines))
                continue

            if line.startswith("    "):
                child_lines.append(next(lines)[4:])
                continue

            if line.startswith("\t"):
                child_lines.append(next(lines)[1:])
                continue

            break

        return class_name, child_lines

    def __init__(self, match: tuple[str, list[str]]) -> None:
        self.class_name, child_lines = match
        self.children = tokenize(child_lines)

class Index(BlockToken):
    pattern = re.compile(r"^ {0,3}:index:\s*$")

    @classmethod
    def start(cls, line: Optional[str]) -> bool:
        if line is None:
            return False
        return cls.pattern.match(line) is not None

    @classmethod
    def check_interrupts_paragraph(cls, lines: _PeekableLines) -> bool:
        return cls.start(lines.peek())

    @classmethod
    def read(cls, lines: _PeekableLines) -> tuple[()]:
        try:
            next(lines)
        except StopIteration:
            return ()
        return ()

    def __init__(self, match: tuple[()]) -> None:
        self.children = []

class StyledownRenderer(HtmlRenderer):
    def __init__(self, index_entries: Iterable[IndexEntry] = ()) -> None:
        super().__init__(Div, Index)
        self.index_entries = list(index_entries)
        self.code_formatter = HtmlFormatter(nowrap=False)

    def render_div(self, token: Any) -> str:
        class_name = html.escape(token.class_name, quote=True)
        inner = self.render_inner(token)
        return f'<div class="{class_name}">\n{inner}\n</div>'

    def render_index(self, token: Any) -> str:
        rows = []

        for entry in self.index_entries:
            href = html.escape(entry.href, quote=True)
            title = render_styledown_inline(entry.title)
            rows.append(
                "<tr>"
                f'<td class="nowrap"><a href="{href}">{title}</a></td>'
                f"<td>{entry.description}</td>"
                "</tr>"
            )

        rows = "\n".join(rows)

        return (
            '<table class="index">\n'
            "<thead>\n"
            "<tr><th>Name</th><th>Description</th></tr>\n"
            "</thead>\n"
            "<tbody>\n"
            f"{rows}\n"
            "</tbody>\n"
            "</table>"
        )

    def render_block_code(self, token: Any) -> str:
        code = token.children[0].content if token.children else ""

        language = getattr(token, "language", None)
        if language:
            language = language.strip().split()[0]

        lexer = get_pygments_lexer(code, language)
        return highlight(code, lexer, self.code_formatter)

def get_pygments_lexer(code: str, language: Optional[str]) -> Lexer:
    if language:
        try:
            return get_lexer_by_name(language)
        except ClassNotFound:
            pass

    try:
        return guess_lexer(code)
    except ClassNotFound:
        return TextLexer()

def escape_styledown_link_text(text: str) -> str:
    if "\n" in text:
        raise Exception("Newline found in link href or text")
    return text.replace("\\", "\\\\").replace("]", "\\]").replace(")", "\\)")

def generate_breadcrumbs_styledown(breadcrumbs: Iterable[Breadcrumb]) -> str:
    crumbs = list(breadcrumbs)
    if not crumbs:
        return ""

    parts = []
    for i, crumb in enumerate(crumbs):
        href = escape_styledown_link_text(crumb.href)
        title = escape_styledown_link_text(crumb.title)
        is_last = i == len(crumbs) - 1
        if is_last:
            parts.append(f"{title}")
        else:
            parts.append(f"[{title}]({href})")

    return ".caption.muted: " + " / ".join(parts) + "\n\n"

def render_styledown(
    styledown: str,
    index_entries: Iterable[IndexEntry] = (),
    breadcrumbs: Iterable[Breadcrumb] = (),
) -> str:
    styledown = generate_breadcrumbs_styledown(breadcrumbs) + styledown
    with StyledownRenderer(index_entries) as renderer:
        return renderer.render(Document(styledown.splitlines(True)))

def render_styledown_inline(styledown: str) -> str:
    class _Inline:
        def __init__(self, children: list[Any]) -> None:
            self.children = children

    with MetadataRenderer() as renderer:
        children = tokenize_inner(styledown)
        return renderer.render_inner(_Inline(children)).strip()

def render_styledown_page(
    styledown: str,
    index_entries: Iterable[IndexEntry] = (),
    breadcrumbs: Iterable[Breadcrumb] = (),
) -> str:
    body = render_styledown(styledown, index_entries, breadcrumbs)
    title = extract_metadata(styledown).title
    template = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    return template.replace("{title}", title).replace("{body}", body)

class MetadataRenderer(StyledownRenderer):
    def __init__(self) -> None:
        super().__init__(index_entries=())

    def render_inline(self, token: Any) -> str:
        return self.render_inner(token).strip()

def walk_tokens(token: Any) -> Iterator[Any]:
    yield token

    children = getattr(token, "children", None) or []
    for child in children:
        yield from walk_tokens(child)

def extract_metadata(styledown: str) -> Metadata:
    with MetadataRenderer() as renderer:
        document = Document(styledown.splitlines(True))

        title_token = None
        description_token = None

        for token in walk_tokens(document):
            if title_token is None and isinstance(token, Heading) and token.level == 1:
                title_token = token

            if description_token is None and isinstance(token, Paragraph):
                description_token = token

            if title_token is not None and description_token is not None:
                break

        title = renderer.render_inline(title_token) if title_token else None
        description = renderer.render_inline(description_token) if description_token else None

    if title is None:
        raise Exception("Title not found")
    if description is None:
        raise Exception("Description not found")

    return Metadata(title=title, description=description)

def get_css() -> str:
    styledown_css = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")
    return styledown_css + "\n" + HtmlFormatter().get_style_defs(".highlight")
