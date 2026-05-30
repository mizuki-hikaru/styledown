import argparse
import os
from pathlib import Path
from urllib.parse import quote

from .server import run_server
from .styledown import metadata, split_frontmatter, styledown

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{styles}</style>
</head>
<body>
{body}
</body>
</html>
"""

BLACKLISTED_DIRECTORY_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
}

def title_from_slug(slug: str) -> str:
    """Compute the HTML page title from a slug."""

    return slug.replace("-", " ").replace("_", " ").title()

def load_styles() -> str:
    """Load the bundled stylesheet contents."""

    return (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")

def escape_markdown_link_text(text: str, for_table: bool = False) -> str:
    """Escape text used inside markdown link labels."""

    escaped = (
        text.replace("\\", "\\\\")
        .replace("]", "\\]")
        .replace("_", "\\_")
        .replace("*", "\\*")
    )
    if for_table:
        escaped = escaped.replace("|", "\\|")
    return escaped

def escape_href(href: str) -> str:
    """URL-escape each path component of a relative href."""

    parts = href.split("/")
    escaped_parts = []
    for part in parts:
        if part in ("", ".", ".."):
            escaped_parts.append(part)
        else:
            escaped_parts.append(quote(part))
    return "/".join(escaped_parts)

def ensure_dir_href(href: str) -> str:
    if href in ("", "."):
        return "./"
    return href if href.endswith("/") else f"{href}/"

def relative_href(from_dir: Path, to_path: Path) -> str:
    """Compute a browser-friendly relative href from one directory to a file."""

    rel = os.path.relpath(to_path, start=from_dir)
    return escape_href(rel.replace(os.sep, "/"))

def breadcrumb_markdown(root_dir: Path, page_path: Path) -> str:
    """Build a breadcrumb line in styledown markdown."""

    root_dir = root_dir.resolve()
    page_path = page_path.resolve()
    page_dir = page_path if page_path.is_dir() else page_path.parent
    rel_path = page_path.relative_to(root_dir)

    crumbs = []
    if page_path == root_dir:
        crumbs.append("Home")
    else:
        home_href = ensure_dir_href(relative_href(page_dir, root_dir))
        crumbs.append(f"[Home]({home_href})")

    current = root_dir
    parts = [part for part in rel_path.parts if part and part != "."]
    for i, part in enumerate(parts):
        current = current / part
        is_last = i == len(parts) - 1

        is_file_page = page_path.is_file()
        label_part = Path(part).stem if is_last and is_file_page else part
        label = escape_markdown_link_text(title_from_slug(label_part))

        if is_last:
            crumbs.append(label)
        else:
            href = ensure_dir_href(relative_href(page_dir, current))
            crumbs.append(f"[{label}]({href})")

    return f".caption.muted: {' / '.join(crumbs)}\n\n"

def directory_listing_markdown(dir_path: Path) -> str:
    """Build a markdown table listing directories and files."""

    entries: list[Path] = []
    for entry in dir_path.iterdir():
        name = entry.name
        if entry.is_dir() and name.lower() in BLACKLISTED_DIRECTORY_NAMES:
            continue
        if entry.is_file() and name.lower() == "index.html":
            continue
        if entry.is_file() and entry.suffix.lower() == ".md":
            continue
        if name.startswith("."):
            continue
        entries.append(entry)

    entries.sort(key=lambda p: (p.is_file(), p.name.lower()))

    if not entries:
        return "No files in this directory.\n"

    lines = ["| Name | Description |", "| ---- | ----------- |"]
    for entry in entries:
        description = ""
        if entry.is_dir():
            label_text = title_from_slug(entry.name)
            href = f"{entry.name}/"
            description = metadata(entry).get("description", "")
        else:
            label_text = title_from_slug(entry.stem)
            href = entry.stem
            if entry.suffix.lower() == ".html":
                source_md = entry.with_suffix(".md")
                if source_md.exists():
                    description = metadata(source_md).get("description", "")

        label = escape_markdown_link_text(label_text, for_table=True)
        desc_cell = escape_markdown_link_text(description, for_table=True)
        lines.append(f"| [{label}]({escape_href(href)}) | {desc_cell} |")

    return "\n".join(lines) + "\n"

def write_html(output_path: Path, title: str, styles: str, body: str) -> None:
    """Write a complete HTML document to output_path."""

    output_path.write_text(
        TEMPLATE.format(title=title, styles=styles, body=body),
        encoding="utf-8",
    )

def convert_markdown_file(md_path: Path, root_dir: Path, styles: str) -> None:
    """Convert a single .md file to a .html file next to it."""

    md_path = md_path.resolve()
    root_dir = root_dir.resolve()

    if md_path.suffix.lower() != ".md":
        raise ValueError(f"Not a markdown file: {md_path}")

    breadcrumb_path = md_path.parent if md_path.name.lower() == "index.md" else md_path
    breadcrumb = breadcrumb_markdown(root_dir, breadcrumb_path)
    _, markdown_body = split_frontmatter(md_path.read_text(encoding="utf-8"))
    markdown_text = breadcrumb + markdown_body

    body = styledown(markdown_text)
    title = title_from_slug(metadata(md_path).get("title") or md_path.stem)

    output_path = md_path.with_suffix(".html")
    write_html(output_path, title, styles, body)

def ensure_directory_index(dir_path: Path, root_dir: Path, styles: str) -> None:
    """Write index.html as a directory listing if index.md is not present."""

    dir_path = dir_path.resolve()
    root_dir = root_dir.resolve()

    index_md = dir_path / "index.md"
    if index_md.exists():
        return

    title = "Home" if dir_path == root_dir else title_from_slug(dir_path.name)
    breadcrumb = breadcrumb_markdown(root_dir, dir_path)
    listing = directory_listing_markdown(dir_path)
    body = styledown(breadcrumb + listing)
    write_html(dir_path / "index.html", title, styles, body)

def convert_tree(root_dir: Path, styles: str) -> int:
    """Convert all markdown files under root_dir, skipping blacklisted directories."""

    root_dir = root_dir.resolve()
    count = 0
    blacklist = {name.lower() for name in BLACKLISTED_DIRECTORY_NAMES}

    for current_dir_str, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d.lower() not in blacklist]
        current_dir = Path(current_dir_str)

        md_files = sorted(
            (current_dir / f for f in filenames if f.lower().endswith(".md")),
            key=lambda p: p.name.lower(),
        )

        for md_path in md_files:
            convert_markdown_file(md_path, root_dir, styles)
            rel = md_path.relative_to(root_dir).as_posix()
            print(f"[+] Converted {rel}")
            count += 1

        ensure_directory_index(current_dir, root_dir, styles)

    return count

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="styledown")
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Markdown file or directory to convert (default: .).",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host interface to bind the server to (default: localhost).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1234,
        help="Port to bind the server to (default: 1234).",
    )
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        raise FileNotFoundError(target)

    styles = load_styles()
    root_dir: Path

    if target.is_dir():
        count = convert_tree(target, styles)
        print(f"[+] Converted {count} files")
        root_dir = target
        run_server(root_dir, host=args.host, port=args.port)
        return 0

    if target.suffix.lower() != ".md":
        raise ValueError(f"File must end with .md: {target}")

    root_dir = target.parent
    convert_markdown_file(target, root_dir, styles)
    print(f"[+] Converted {target.name}")
    ensure_directory_index(root_dir, root_dir, styles)
    print("[+] Converted 1 files")
    run_server(root_dir, host=args.host, port=args.port)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
