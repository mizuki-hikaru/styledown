import re
import posixpath
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

def _path_within_root(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def _sanitize_url_path(url_path: str) -> Optional[Path]:
    normalized = url_path.lstrip("/")
    normalized = posixpath.normpath(normalized)

    if normalized in ("", "."):
        return Path("")

    parts = [p for p in normalized.split("/") if p]
    if any(p == ".." for p in parts):
        return None

    return Path(*parts)

def _sanitize_host(host: str) -> Optional[str]:
    host = host.strip().lower()
    if not host:
        return None

    match = re.fullmatch(
        r"(?P<name>"
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r")"
        r"\.?"
        r"(?::(?P<port>[0-9]+))?",
        host,
    )
    if match is None:
        return None

    name = match.group("name")

    if len(name) > 253:
        return None

    return name

def create_app(root: Path, domains: bool = False) -> FastAPI:
    root = root.resolve()
    app = FastAPI()

    @app.get("/{url_path:path}")
    async def get_page(url_path: str, request: Request):
        request_root = root
        if domains:
            host = _sanitize_host(request.headers.get("host", ""))
            if host is None:
                raise HTTPException(status_code=404)
            request_root = (root / host).resolve(strict=False)
            if not _path_within_root(root, request_root) or not request_root.is_dir():
                raise HTTPException(status_code=404)

        requested_rel = _sanitize_url_path(url_path)
        if requested_rel is None:
            raise HTTPException(status_code=404)

        requested = (request_root / requested_rel).resolve(strict=False)
        if not _path_within_root(request_root, requested):
            raise HTTPException(status_code=404)

        if requested.is_file():
            return FileResponse(requested)

        if (
            requested_rel.as_posix() not in ("", ".")
            and requested.name
            and not requested.name.lower().endswith(".html")
        ):
            html_candidate = (requested.parent / f"{requested.name}.html").resolve(strict=False)
            if _path_within_root(request_root, html_candidate) and html_candidate.is_file():
                return FileResponse(html_candidate)

        if requested.is_dir():
            index_candidate = (requested / "index.html").resolve(strict=False)
            if _path_within_root(request_root, index_candidate) and index_candidate.is_file():
                return FileResponse(index_candidate)

        raise HTTPException(status_code=404)

    return app

def run_server(root: Path, host: str, port: int, domains: bool = False) -> None:
    import uvicorn

    app = create_app(root, domains=domains)
    uvicorn.run(app, host=host, port=port)
