import argparse
import html
import mimetypes
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote, unquote, urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .code_extensions import CODE_VIEW_SUFFIXES
from .markdown import markdown

HOST_RE = re.compile(r"^[a-z0-9.-]+$")

ASSET_DIR = Path(__file__).resolve().parent

BLACKLISTED_PATH_COMPONENTS = {
    ".ds_store",
    ".env",
    ".git",
    ".hg",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "env",
    "node_modules",
    "venv",
}

def enforce_path_component_blacklist(url_rel: Path) -> None:
    """Reject requests containing blacklisted path components.

    This is an explicit safety boundary intended for serving public websites.
    """

    for part in url_rel.parts:
        if not part or part == ".":
            continue
        if part.lower() in BLACKLISTED_PATH_COMPONENTS:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="Not found")

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
{body}
</body>
</html>
"""

@dataclass(frozen=True)
class ResolvedPath:
    """Filesystem candidate resolved from a request path."""

    path: Path
    should_render: bool

@dataclass(frozen=True)
class ServerConfig:
    """Configuration for the HTTP server."""

    root_dir: Path
    domains: bool

def normalize_media_type(value: Optional[str]) -> Optional[str]:
    """Ensure text/* responses include a UTF-8 charset."""

    if value and value.startswith("text/") and "charset=" not in value:
        return f"{value}; charset=utf-8"
    return value

def is_path_within_root(root_dir: Path, candidate_path: Path) -> bool:
    """Return True if candidate_path is inside root_dir after resolving symlinks."""

    root_dir = root_dir.resolve()
    candidate_path = candidate_path.resolve()
    try:
        candidate_path.relative_to(root_dir)
    except ValueError:
        return False
    return True

def is_safe_candidate(root_dir: Path, candidate: ResolvedPath) -> bool:
    """Return True if the candidate file stays within root_dir."""

    return is_path_within_root(root_dir, candidate.path)

def parse_url_path(url_path: str) -> Path:
    """Parse a URL path (e.g. '/a/b/') into a relative Path ('a/b')."""

    raw_path = unquote(urlparse(url_path).path)
    return Path(raw_path.lstrip("/"))

def escape_markdown_link_text(text: str, *, for_table: bool = False) -> str:
    """Escape link text used inside markdown rendered by mistletoe."""

    escaped = (
        html.escape(text)
        .replace("\\", "\\\\")
        .replace("]", "\\]")
        .replace("_", "\\_")
        .replace("*", "\\*")
    )
    if for_table:
        escaped = escaped.replace("|", "\\|")
    return escaped

def url_from_parts(parts: tuple[str, ...], *, trailing_slash: bool) -> str:
    """Build a URL path from already-decoded parts, URL-quoting each segment."""

    quoted = "/".join(quote(p) for p in parts if p)
    if quoted == "":
        return "/" if trailing_slash else ""
    return f"/{quoted}/" if trailing_slash else f"/{quoted}"

def markdown_source_url(url_path: str) -> str:
    """Compute the URL to the raw markdown source for a rendered markdown page."""

    if url_path.endswith("/"):
        return f"{url_path}index.md"
    return f"{url_path}.md"

def resolve_request_path(url_rel: Path, root_dir: Path, host: str = ".") -> Optional[ResolvedPath]:
    """Resolve a URL path to a filesystem path candidate within root_dir.

    Returns None if the request path attempts to escape root_dir.
    Returns a ResolvedPath even if it doesn't exist, so callers can return 404.
    """

    fs_path = (root_dir / Path(host) / Path(url_rel.as_posix())).resolve()

    if fs_path.is_dir():
        candidates = [
            ResolvedPath(path=fs_path / "index.md", should_render=True),
            ResolvedPath(path=fs_path / "index.html", should_render=False),
        ]
    else:
        if fs_path.suffix:
            candidates = [ResolvedPath(path=fs_path, should_render=False)]
        else:
            candidates = [
                ResolvedPath(path=fs_path, should_render=False),
                ResolvedPath(path=fs_path.with_suffix(".md"), should_render=True),
            ]

    for candidate in candidates:
        if not is_safe_candidate(root_dir, candidate):
            return None
        if candidate.path.exists() and candidate.path.is_file():
            return candidate

    return candidate

def build_breadcrumb(url_rel: Path, include_file: bool = False) -> str:
    """Render a breadcrumb navigation line for the current URL."""

    parts = [p for p in url_rel.parts if p and p != "."]
    if parts and parts[-1].lower() in ("index.md", "index.html"):
        parts = parts[:-1]
        include_file = False

    crumbs = ["[Home](/)"]
    current_parts: list[str] = []

    for i, part in enumerate(parts):
        current_parts.append(part)
        is_last = i == len(parts) - 1
        if is_last and include_file:
            crumbs.append(escape_markdown_link_text(part))
        else:
            label = escape_markdown_link_text(part)
            href = url_from_parts(tuple(current_parts), trailing_slash=True)
            crumbs.append(f"[{label}]({href})")

    text = " / ".join(crumbs)
    return f".caption.muted: {text}\n\n"

def render_directory_listing(dir_path: Path, url_rel: Path, root_dir: Path) -> str:
    """Render a directory listing as a markdown table (or an empty message)."""

    root_dir = Path(root_dir).resolve()
    dir_path = Path(dir_path).resolve()
    title = "Home" if dir_path == root_dir else dir_path.name
    items = []
    for entry in dir_path.iterdir():
        name = entry.name
        if name.lower() in BLACKLISTED_PATH_COMPONENTS:
            continue
        items.append((not entry.is_dir(), name, entry))

    items.sort(key=lambda t: (t[0], t[1].lower()))

    base_url = url_from_parts(tuple(p for p in url_rel.parts if p and p != "."), trailing_slash=False)

    lines = [build_breadcrumb(url_rel)]
    if not items:
        lines.append("No files in this directory.")
    else:
        lines.append("| Name |")
        lines.append("| ---- |")
        for _, name, entry in items:
            is_dir = entry.is_dir()
            if (not is_dir) and entry.suffix.lower() == ".md":
                link_name = entry.stem
            else:
                link_name = name

            display = name + ("/" if is_dir else "")
            href = f"{base_url}/{quote(link_name)}" + ("/" if is_dir else "")
            label = escape_markdown_link_text(display, for_table=True)
            lines.append(f"| [{label}]({href}) |")

    body = markdown("\n".join(lines))
    return TEMPLATE.format(title=html.escape(title), body=body)

def render_markdown(markdown_path: Path, url_rel: Path) -> str:
    """Render a markdown file into the HTML page template."""

    if markdown_path.name.lower() == "index.md":
        breadcrumb_url_rel = url_rel
        include_file = False
    else:
        breadcrumb_url_rel = url_rel.with_suffix(".md")
        include_file = True
    breadcrumb = build_breadcrumb(breadcrumb_url_rel, include_file)
    text = markdown_path.read_text(encoding="utf-8")
    rendered = markdown(breadcrumb + text, filename=markdown_path.name)
    title = html.escape(markdown_path.stem.replace("-", " ").replace("_", " ").title())
    return TEMPLATE.format(title=title, body=rendered)

def render_code(code_path: Path, url_rel: Path) -> str:
    """Render a code file with breadcrumbs and syntax highlighting."""

    text = code_path.read_text(encoding="utf-8", errors="replace")
    indented = "\n".join(f"    {line}" for line in text.splitlines())
    breadcrumb = build_breadcrumb(url_rel, include_file=True)
    header = escape_markdown_link_text(code_path.name)
    body = markdown(f"{breadcrumb}\n\n# {header}\n\n{indented}", filename=code_path.name)
    title = html.escape(code_path.name)
    return TEMPLATE.format(title=title, body=body)

def serve_resolved_path(resolved: ResolvedPath, url_rel: Path, url_path: str) -> Union[FileResponse, HTMLResponse]:
    """Serve a resolved path as rendered HTML or a raw file response."""

    path = resolved.path
    suffix = path.suffix.lower()

    if suffix == ".md" and resolved.should_render:
        return HTMLResponse(render_markdown(path, url_rel))

    if suffix in CODE_VIEW_SUFFIXES:
        return HTMLResponse(render_code(path, url_rel))

    content_type, _ = mimetypes.guess_type(path.name)
    content_type = normalize_media_type(content_type or "application/octet-stream")
    return FileResponse(path, media_type=content_type)

def get_domain_host(request: Request) -> str:
    """Extract and validate the host from the Host header."""

    host_header = request.headers.get("host")
    if not host_header:
        raise HTTPException(status_code=400, detail="Host header required")

    host = host_header.split(":", 1)[0].strip().lower()
    if not host or not HOST_RE.fullmatch(host):
        raise HTTPException(status_code=400, detail="Invalid host")

    return host

def create_app(config: ServerConfig) -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI()
    app.state.config = config

    @app.get("/styles.css")
    def styles():
        """Serve the bundled stylesheet."""

        style_path = ASSET_DIR / "styles.css"
        if not style_path.exists() or not style_path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        content_type, _ = mimetypes.guess_type(style_path.name)
        content_type = normalize_media_type(content_type or "application/octet-stream")
        return FileResponse(style_path, media_type=content_type)

    @app.get("/")
    @app.get("/{path:path}")
    def route(path, request: Request):
        config = request.app.state.config
        host = get_domain_host(request) if config.domains else "."
        url_path = request.url.path
        url_rel = parse_url_path(url_path)
        enforce_path_component_blacklist(url_rel)
        fs_path = (config.root_dir / Path(host) / Path(url_rel.as_posix())).resolve()
        if is_path_within_root(config.root_dir, fs_path) and fs_path.exists() and fs_path.is_dir():
            index_md = fs_path / "index.md"
            index_html = fs_path / "index.html"
            if not index_md.exists() and not index_html.exists():
                return HTMLResponse(render_directory_listing(fs_path, url_rel, config.root_dir))

        result = resolve_request_path(url_rel, config.root_dir, host)
        if result is None:
            return PlainTextResponse("Forbidden", status_code=HTTPStatus.FORBIDDEN)
        if not result.path.exists() or not result.path.is_file():
            return PlainTextResponse("Not found", status_code=HTTPStatus.NOT_FOUND)
        return serve_resolved_path(result, url_rel, url_path)

    return app

def main(argv=None) -> int:
    """CLI entry-point for mdserver."""

    parser = argparse.ArgumentParser(prog="mdserver")
    parser.add_argument(
        "--domains",
        action="store_true",
        help="Serve from <root>/<host>/... using the Host header.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory to serve from (default: .).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host/interface to bind to (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1234,
        help="Port to bind to (default: 1234).",
    )

    if argv is None:
        argv = sys.argv[1:]

    args = parser.parse_args(argv)

    if not (1 <= args.port <= 65535):
        print("PORT must be between 1 and 65535", file=sys.stderr)
        return 2

    root_dir = Path(args.root).resolve()
    if not root_dir.exists() or not root_dir.is_dir():
        print(f"Root directory does not exist or is not a directory: {root_dir}", file=sys.stderr)
        return 2

    config = ServerConfig(root_dir=root_dir, domains=args.domains)
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
