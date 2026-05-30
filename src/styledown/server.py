import posixpath
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
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

def create_app(root: Path) -> FastAPI:
    root = root.resolve()
    app = FastAPI()

    @app.get("/{url_path:path}")
    async def get_page(url_path: str):
        requested_rel = _sanitize_url_path(url_path)
        if requested_rel is None:
            raise HTTPException(status_code=404)

        requested = (root / requested_rel).resolve(strict=False)
        if not _path_within_root(root, requested):
            raise HTTPException(status_code=404)

        if requested.is_file():
            return FileResponse(requested)

        if (
            requested_rel.as_posix() not in ("", ".")
            and requested.name
            and not requested.name.lower().endswith(".html")
        ):
            html_candidate = (requested.parent / f"{requested.name}.html").resolve(strict=False)
            if _path_within_root(root, html_candidate) and html_candidate.is_file():
                return FileResponse(html_candidate)

        if requested.is_dir():
            index_candidate = (requested / "index.html").resolve(strict=False)
            if _path_within_root(root, index_candidate) and index_candidate.is_file():
                return FileResponse(index_candidate)

        raise HTTPException(status_code=404)

    return app

def run_server(root: Path, host: str, port: int) -> None:
    import uvicorn

    app = create_app(root)
    uvicorn.run(app, host=host, port=port)
