import re
from typing import Optional

import mistletoe
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer, guess_lexer_for_filename
from pygments.lexers.special import TextLexer
from pygments.util import ClassNotFound

# Matches:
# .card:
# .card.big:
# .card.big: inline content
DIV_RE = re.compile(
    r"^(\s*)((?:\.[A-Za-z_][\w-]*)+):(.*)$"
)


def indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_classes(class_expr: str) -> str:
    return class_expr.replace(".", " ").strip()


def first_child_indent(
    lines: list[str],
    start: int,
    base_indent: int,
) -> int:
    for i in range(start, len(lines)):
        line = lines[i]

        if not line.strip():
            continue

        indent = indent_width(line)

        if indent <= base_indent:
            raise ValueError(
                f"Line {i + 1}: div block has no indented content"
            )

        return indent

    raise ValueError(
        f"Line {start}: div block has no content"
    )


def preprocess_div_blocks(text: str) -> str:
    lines = text.splitlines()
    output = []

    stack = []  # [{"base_indent": int, "content_indent": int}]

    for i, line in enumerate(lines):
        line_number = i + 1

        raw_indent = indent_width(line)
        stripped = line.lstrip()

        while stack and stripped and raw_indent <= stack[-1]["base_indent"]:
            output.append("")
            output.append("</div>")
            output.append("")
            stack.pop()

        # Ensure code blocks render correctly.
        if stripped:
            if stack:
                current = stack[-1]
                if raw_indent >= current["content_indent"] + 4:
                    output.append(line[current["content_indent"]:])
                    continue
            else:
                if raw_indent >= 4:
                    output.append(line)
                    continue

        match = DIV_RE.match(line)

        if match:
            indent_str, class_expr, inline_content = match.groups()

            base_indent = len(indent_str)
            classes = parse_classes(class_expr)
            inline_content = inline_content.lstrip()

            # Inline form:
            # .card.big: hello
            if inline_content:
                output.append("")
                output.append(f'<div class="{classes}">')
                output.append("")
                output.append(inline_content)
                output.append("")
                output.append("</div>")
                output.append("")

            # Block form:
            # .card.big:
            #     hello
            else:
                content_indent = first_child_indent(
                    lines,
                    i + 1,
                    base_indent,
                )

                output.append("")
                output.append(f'<div class="{classes}">')
                output.append("")

                stack.append({
                    "base_indent": base_indent,
                    "content_indent": content_indent,
                })

            continue

        if stack and stripped:
            current = stack[-1]

            if raw_indent < current["content_indent"]:
                raise ValueError(
                    f"Line {line_number}: invalid indentation "
                    f"(got {raw_indent}, expected >= "
                    f"{current['content_indent']})"
                )

            line = line[current["content_indent"]:]

        output.append(line)

    while stack:
        output.append("")
        output.append("</div>")
        output.append("")
        stack.pop()

    return "\n".join(output)


class PygmentsHtmlRenderer(mistletoe.HtmlRenderer):
    formatter = HtmlFormatter(noclasses=True)

    def __init__(self, filename=None, *extras, **kwargs):
        super().__init__(*extras, **kwargs)
        self._filename = filename

    def render_block_code(self, token):
        code = token.content

        lexer = None
        if token.language:
            try:
                lexer = get_lexer_by_name(token.language)
            except ClassNotFound:
                lexer = None

        if lexer is None and self._filename:
            try:
                lexer = guess_lexer_for_filename(self._filename, code)
            except ClassNotFound:
                lexer = None

        if lexer is None:
            try:
                lexer = guess_lexer(code)
            except ClassNotFound:
                lexer = TextLexer()

        return highlight(code, lexer, self.formatter)


def markdown(text: str, filename: Optional[str] = None) -> str:
    text = preprocess_div_blocks(text)
    return mistletoe.markdown(text, renderer=lambda: PygmentsHtmlRenderer(filename=filename))
