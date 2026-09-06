from __future__ import annotations

import uvicorn

from .app import create_app
from .bootstrap import bootstrap_plaintext_metadata_credentials
from .config import Settings


def main() -> None:
    bootstrap_plaintext_metadata_credentials()
    settings = Settings.from_env()
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
