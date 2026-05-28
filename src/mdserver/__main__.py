import argparse
import html
import mimetypes
import re
import sys
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from socketserver import TCPServer, StreamRequestHandler
from typing import Optional
from urllib.parse import unquote, urlparse

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


def response(status, body, content_type="text/html; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode("utf-8")

    if content_type and content_type.startswith("text/") and "charset=" not in content_type:
        content_type = f"{content_type}; charset=utf-8"

    return (
        f"HTTP/1.1 {status.value} {status.phrase}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body


@dataclass(frozen=True)
class ResolvedPath:
    path: Path
    should_render: bool


@dataclass(frozen=True)
class ServerConfig:
    root_dir: Path
    domains: bool


def is_safe_candidate(root_path, candidate):
    try:
        candidate.path.relative_to(root_path)
    except ValueError:
        return False
    return True


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


def render_markdown(markdown_path):
    text = markdown_path.read_text(encoding="utf-8")
    rendered = markdown(text)
    title = html.escape(markdown_path.stem.replace("-", " ").replace("_", " ").title())
    page = TEMPLATE.format(title=title, body=rendered)
    return page


def render_code(code_path):
    text = code_path.read_text(encoding="utf-8", errors="replace")
    indented = "\n".join(f"    {line}" for line in text.splitlines())
    body = markdown(f"# {code_path.name}\n\n{indented}", filename=code_path.name)
    title = html.escape(code_path.name)
    return TEMPLATE.format(title=title, body=body)


RENDERERS = {".md": render_markdown}


class Handler(StreamRequestHandler):
    config: Optional[ServerConfig] = None

    def handle(self):
        config = self.config
        if config is None:
            self.wfile.write(response(HTTPStatus.INTERNAL_SERVER_ERROR, "Server misconfigured"))
            return

        first_line = self.rfile.readline(8192).decode("iso-8859-1").strip()

        parts = first_line.split()
        if len(parts) != 3:
            self.wfile.write(response(HTTPStatus.BAD_REQUEST, "Bad request"))
            return

        method, target, version = parts

        headers = {}

        while True:
            line = self.rfile.readline(8192).decode("iso-8859-1")

            if line in ("\r\n", "\n", ""):
                break

            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip().lower()

        host_header = headers.get("host")
        host = None
        if config.domains:
            if not host_header:
                self.wfile.write(response(HTTPStatus.BAD_REQUEST, "Host header required"))
                return

            host = host_header.split(":", 1)[0].strip().lower()
            if not host or not HOST_RE.fullmatch(host):
                self.wfile.write(response(HTTPStatus.BAD_REQUEST, "Invalid host"))
                return

        if method != "GET":
            self.wfile.write(response(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed"))
            return

        requested_url_path = urlparse(target).path
        if requested_url_path == "/styles.css":
            style_path = ASSET_DIR / "styles.css"
            if not style_path.exists() or not style_path.is_file():
                self.wfile.write(response(HTTPStatus.NOT_FOUND, "Not found"))
                return

            content_type, _ = mimetypes.guess_type(style_path.name)
            self.wfile.write(
                response(
                    HTTPStatus.OK,
                    style_path.read_bytes(),
                    content_type or "application/octet-stream",
                )
            )
            return

        result = safe_path(target, config.root_dir, host if config.domains else ".")

        if result is None:
            self.wfile.write(response(HTTPStatus.FORBIDDEN, "Forbidden"))
            return
        path = result.path
        should_render = result.should_render

        if not path.exists() or not path.is_file():
            self.wfile.write(response(HTTPStatus.NOT_FOUND, "Not found"))
            return

        suffix = path.suffix.lower()
        renderer = RENDERERS.get(suffix) if should_render else None
        if renderer is not None:
            self.wfile.write(response(HTTPStatus.OK, renderer(path)))
            return

        if suffix in CODE_VIEW_SUFFIXES:
            self.wfile.write(response(HTTPStatus.OK, render_code(path)))
            return

        content_type, _ = mimetypes.guess_type(path.name)
        self.wfile.write(
            response(
                HTTPStatus.OK,
                path.read_bytes(),
                content_type or "application/octet-stream",
            )
        )


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
    Handler.config = config
    host, port = args.host, args.port

    with TCPServer((host, port), Handler) as server:
        if args.domains:
            print(f"Serving domains from {root_dir} on http://{host}:{port}/")
        else:
            print(f"Serving from {root_dir} on http://{host}:{port}/")
        server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
