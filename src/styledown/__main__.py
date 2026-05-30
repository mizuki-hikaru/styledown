import argparse
import os
import shutil
from pathlib import Path
from typing import Union
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

        is_md = page_path.is_file() and page_path.suffix == ".md"
        label_part = Path(part).stem if is_last and is_md else part
        label = escape_markdown_link_text(metadata(current)["title"])

        if is_last:
            crumbs.append(label)
        else:
            href = ensure_dir_href(relative_href(page_dir, current))
            crumbs.append(f"[{label}]({href})")

    return f".caption.muted: {' / '.join(crumbs)}\n\n"

def directory_listing_markdown(dir_path: Path) -> str:
    """Build a markdown table listing directories and files."""

    class MetaEntry:
        def __init__(self, name: str, url: str, description: str):
            self.name = name
            self.url = url
            self.description = description

    entries: list[Union[Path, MetaEntry]] = []
    for entry in dir_path.iterdir():
        name = entry.name
        if entry.is_dir() and name.lower() in BLACKLISTED_DIRECTORY_NAMES:
            continue
        if entry.is_file() and name.lower() == "index.md":
            continue
        if name.startswith("."):
            continue
        entries.append(entry)

    dir_meta = metadata(dir_path)
    links = dir_meta.get("links", [])
    for item in links:
        label = item.get("label", "").strip()
        url = item.get("url", "").strip()
        description = item.get("description", "").strip()
        if not label or not url:
            continue
        entries.append(MetaEntry(label, url, description))

    def get_name(entry):
        if isinstance(entry, MetaEntry):
            return entry.name
        return metadata(entry)["title"]

    entries.sort(key=get_name)

    if not entries:
        return "No files in this directory.\n"

    lines = ["| Name | Description |", "| ---- | ----------- |"]
    for entry in entries:
        label_text = get_name(entry)
        if isinstance(entry, MetaEntry):
            href = entry.url
            description = entry.description
        elif entry.is_dir():
            href = f"{entry.name}/"
            description = metadata(entry).get("description", "")
        elif entry.suffix.lower() == ".md":
            href = entry.stem
            description = metadata(entry).get("description", "")
        else:
            href = entry.name
            description = ""

        label = escape_markdown_link_text(label_text, for_table=True)
        desc_cell = escape_markdown_link_text(description, for_table=True)
        href_cell = href if isinstance(entry, MetaEntry) else escape_href(href)
        lines.append(f"| <span class='nowrap'>[{label}]({href_cell})</span> | {desc_cell} |")

    return "\n".join(lines) + "\n"

def write_html(output_path: Path, title: str, styles: str, body: str) -> None:
    """Write a complete HTML document to output_path."""

    output_path.write_text(
        TEMPLATE.format(title=title, styles=styles, body=body),
        encoding="utf-8",
    )

def remove_dist_dir(dist_root: Path) -> None:
    shutil.rmtree(dist_root, ignore_errors=True)

def convert_markdown_file(md_path: Path, root_dir: Path, output_dir: Path, styles: str) -> None:
    """Convert a single .md file to a .html file in the corresponding location under the output_path directory."""

    md_path = md_path.resolve()
    root_dir = root_dir.resolve()
    output_dir = output_dir.resolve()

    if md_path.suffix.lower() != ".md":
        raise ValueError(f"Not a markdown file: {md_path}")

    breadcrumb_path = md_path.parent if md_path.name.lower() == "index.md" else md_path
    breadcrumb = breadcrumb_markdown(root_dir, breadcrumb_path)
    _, markdown_body = split_frontmatter(md_path.read_text(encoding="utf-8"))
    markdown_text = breadcrumb + markdown_body

    body = styledown(markdown_text)
    title = metadata(md_path)["title"]

    output_path = (output_dir / md_path.relative_to(root_dir)).with_suffix(".html")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_html(output_path, title, styles, body)

def ensure_directory_index(dir_path: Path, root_dir: Path, output_dir: Path, styles: str) -> None:
    """Write index.html as a directory listing if index.md is not present."""

    dir_path = dir_path.resolve()
    root_dir = root_dir.resolve()
    output_dir = output_dir.resolve()

    index_md = dir_path / "index.md"
    if index_md.exists():
        return

    title = "Home" if dir_path == root_dir else metadata(dir_path)["title"]
    breadcrumb = breadcrumb_markdown(root_dir, dir_path)
    listing = directory_listing_markdown(dir_path)
    body = styledown(breadcrumb + listing)

    output_path = output_dir / dir_path.relative_to(root_dir) / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_html(output_path, title, styles, body)

def convert_tree(root_dir: Path, output_dir: Path, styles: str) -> int:
    """Convert all markdown files under root_dir, skipping blacklisted directories."""

    root_dir = root_dir.resolve()
    output_dir = output_dir.resolve()
    count = 0
    blacklist = {name.lower() for name in BLACKLISTED_DIRECTORY_NAMES}

    for current_dir_str, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d.lower() not in blacklist and not d.startswith(".")]
        current_dir = Path(current_dir_str)

        for d in list(dirnames):
            src_dir = current_dir / d
            if not src_dir.is_symlink():
                continue
            dst_dir = output_dir / current_dir.relative_to(root_dir) / d
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            dst_dir.symlink_to(os.readlink(src_dir), target_is_directory=True)
            print(f"[+] Copied {dst_dir.relative_to(output_dir).as_posix()}")
            count += 1
            dirnames.remove(d)

        files = [current_dir / f for f in filenames]

        for path in files:
            if path.name.startswith("."):
                continue
            if path.suffix == ".md":
                convert_markdown_file(path, root_dir, output_dir, styles)
                rel = path.relative_to(root_dir).as_posix()
                print(f"[+] Converted {rel}")
                count += 1
            else:
                rel = path.relative_to(root_dir)
                dst_path = output_dir / rel
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_symlink():
                    dst_path.symlink_to(os.readlink(path), target_is_directory=False)
                    print(f"[+] Copied {rel.as_posix()}")
                    count += 1
                else:
                    shutil.copy2(path, dst_path)
                    print(f"[+] Copied {rel.as_posix()}")
                    count += 1

        ensure_directory_index(current_dir, root_dir, output_dir, styles)

    return count

def convert_domains_tree(root_dir: Path, output_dir: Path, styles: str) -> int:
    count = 0
    blacklist = {name.lower() for name in BLACKLISTED_DIRECTORY_NAMES}

    for entry in root_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.name.lower() in blacklist:
            continue
        if not entry.is_dir():
            continue

        out_site_dir = output_dir / entry.name
        if entry.is_symlink():
            out_site_dir.symlink_to(os.readlink(entry), target_is_directory=True)
            continue

        count += convert_tree(entry, out_site_dir, styles)

    return count

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="styledown")
    parser.add_argument(
        "--src",
        default="./src/",
        help="Markdown file or directory to convert (default: ./src/).",
    )
    parser.add_argument(
        "--out",
        default="./dist/",
        help="Directory to place the converted files (default: ./dist/).",
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
    parser.add_argument(
        "--domains",
        action="store_true",
        help="Serve multiple sites from subdirectories by mapping the request Host header to a subdirectory.",
    )
    args = parser.parse_args(argv)

    target = Path(args.src)
    if not target.exists():
        raise FileNotFoundError(target)

    dist_root = Path(args.out).resolve()
    print(f"[+] Removing {args.out}")
    remove_dist_dir(dist_root)
    dist_root.mkdir(parents=True, exist_ok=True)

    styles = load_styles()
    root_dir: Path

    if not target.is_dir():
        raise ValueError(f"Path must be a directory: {target}")

    if args.domains:
        count = convert_domains_tree(target, dist_root, styles)
    else:
        count = convert_tree(target, dist_root, styles)
    print(f"[+] Converted {count} files")
    run_server(dist_root, host=args.host, port=args.port, domains=args.domains)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
