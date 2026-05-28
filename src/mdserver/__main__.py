import argparse
import html
import mimetypes
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from .code_extensions import CODE_VIEW_SUFFIXES
from .markdown import markdown

HOST_RE = re.compile(r"^[a-z0-9.-]+$")

ASSET_DIR = Path(__file__).resolve().parent

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
    path: Path
    should_render: bool

@dataclass(frozen=True)
class ServerConfig:
    root_dir: Path
    domains: bool

def normalize_media_type(value):
    if value and value.startswith("text/") and "charset=" not in value:
        return f"{value}; charset=utf-8"
    return value

def is_safe_path(root_path, path):
    root_path = root_path.resolve()
    path = path.resolve()
    try:
        path.relative_to(root_path)
    except ValueError:
        return False
    return True

def is_safe_candidate(root_path, candidate):
    return is_safe_path(root_path, candidate.path)

def safe_path(url_path, root_path, host="."):
    raw_path = unquote(urlparse(url_path).path)
    path = (root_path / Path(host) / Path(raw_path.lstrip("/"))).resolve()

    if path.is_dir():
        candidates = [
            ResolvedPath(path=path / "index.md", should_render=True),
            ResolvedPath(path=path / "index.html", should_render=False),
        ]
    else:
        if path.suffix:
            candidates = [ResolvedPath(path=path, should_render=False)]
        else:
            candidates = [
                ResolvedPath(path=path, should_render=False),
                ResolvedPath(path=path.with_suffix(".md"), should_render=True),
            ]

    for candidate in candidates:
        if not is_safe_candidate(root_path, candidate):
            return None
        if candidate.path.exists() and candidate.path.is_file():
            return candidate

    return candidate

def build_breadcrumb(url_path, include_file=False):
    parts = [p for p in url_path.strip("/").split("/") if p]
    if parts and parts[-1].lower() in ("index.md", "index.html"):
        parts = parts[:-1]
        include_file = False

    crumbs = ["[Home](/)"]
    current = ""

    for i, part in enumerate(parts):
        current += "/" + quote(part)
        is_last = i == len(parts) - 1
        if is_last and include_file:
            crumbs.append(html.escape(part))
        else:
            label = html.escape(part).replace("\\", "\\\\").replace("]", "\\]")
            crumbs.append(f"[{label}]({current}/)")

    text = " / ".join(crumbs)
    return f".caption.muted: {text}\n\n"

def render_directory_listing(dir_path, url_path, root_dir):
    root_dir = Path(root_dir).resolve()
    dir_path = Path(dir_path).resolve()
    title = "Home" if dir_path == root_dir else dir_path.name
    items = []
    for entry in dir_path.iterdir():
        name = entry.name
        items.append((not entry.is_dir(), name, entry))

    items.sort(key=lambda t: (t[0], t[1].lower()))

    base = url_path.rstrip("/")

    lines = [build_breadcrumb(url_path)]
    if not items:
        lines.append("No files in this directory.")
    else:
        lines.append("| Name |")
        lines.append("| ---- |")
        for _, name, entry in items:
            is_dir = entry.is_dir()
            display = name + ("/" if is_dir else "")
            href = f"{base}/{quote(name)}" + ("/" if is_dir else "")
            label = html.escape(display).replace("\\", "\\\\").replace("]", "\\]").replace("|", "\\|")
            lines.append(f"| [{label}]({href}) |")

    body = markdown("\n".join(lines))
    return TEMPLATE.format(title=html.escape(title), body=body)

def render_markdown(markdown_path):
    text = markdown_path.read_text(encoding="utf-8")
    rendered = markdown(text)
    title = html.escape(markdown_path.stem.replace("-", " ").replace("_", " ").title())
    return TEMPLATE.format(title=title, body=rendered)

def render_code(code_path, url_path):
    text = code_path.read_text(encoding="utf-8", errors="replace")
    indented = "\n".join(f"    {line}" for line in text.splitlines())
    breadcrumb = build_breadcrumb(url_path, include_file=True)
    body = markdown(f"{breadcrumb}\n\n# {code_path.name}\n\n{indented}", filename=code_path.name)
    title = html.escape(code_path.name)
    return TEMPLATE.format(title=title, body=body)

def serve_path(resolved, config, url_path):
    path = resolved.path
    suffix = path.suffix.lower()

    if suffix == ".md" and resolved.should_render:
        return HTMLResponse(render_markdown(path))

    if suffix in CODE_VIEW_SUFFIXES:
        return HTMLResponse(render_code(path, url_path))

    content_type, _ = mimetypes.guess_type(path.name)
    content_type = normalize_media_type(content_type or "application/octet-stream")
    return FileResponse(path, media_type=content_type)

def get_domain_host(request):
    host_header = request.headers.get("host")
    if not host_header:
        raise HTTPException(status_code=400, detail="Host header required")

    host = host_header.split(":", 1)[0].strip().lower()
    if not host or not HOST_RE.fullmatch(host):
        raise HTTPException(status_code=400, detail="Invalid host")

    return host

def create_app(config):
    app = FastAPI()
    app.state.config = config

    @app.get("/styles.css")
    def styles():
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
        fs_path = (config.root_dir / Path(host) / Path(url_path.lstrip("/"))).resolve()
        if is_safe_path(config.root_dir, fs_path) and fs_path.exists() and fs_path.is_dir():
            index_md = fs_path / "index.md"
            index_html = fs_path / "index.html"
            if not index_md.exists() and not index_html.exists():
                return HTMLResponse(render_directory_listing(fs_path, url_path, config.root_dir))

        result = safe_path(url_path, config.root_dir, host)
        if result is None:
            return PlainTextResponse("Forbidden", status_code=HTTPStatus.FORBIDDEN)
        if not result.path.exists() or not result.path.is_file():
            return PlainTextResponse("Not found", status_code=HTTPStatus.NOT_FOUND)
        return serve_path(result, config, url_path)

    return app

def main(argv=None):
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

if __name__ == "__main__":
    raise SystemExit(main())
