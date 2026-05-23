"""Entry point: ``gitrepos-connector`` or ``python -m gitrepos_connector``."""

from __future__ import annotations

import uvicorn
from ironrag_connector import build_app

from .adapter import GitReposAdapter
from .config import GitReposSettings


def main() -> None:
    settings = GitReposSettings()  # type: ignore[call-arg]
    adapter = GitReposAdapter(settings)

    app = build_app(settings, adapter)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
