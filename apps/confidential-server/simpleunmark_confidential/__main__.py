from __future__ import annotations

import uvicorn

from .app import create_app
from .bootstrap import bootstrap_credentials
from .config import Settings


def main() -> None:
    settings = Settings.from_env(credentials=bootstrap_credentials())
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
