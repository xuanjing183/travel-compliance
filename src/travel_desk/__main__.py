"""Single documented start command: `uv run travel-desk`."""
from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    host = os.environ.get("TRAVEL_DESK_HOST", "127.0.0.1")
    port = int(os.environ.get("TRAVEL_DESK_PORT", "8000"))
    # App loggers (startup recovery, workflow failures) carry no payloads or secrets, only ids and types.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    uvicorn.run("travel_desk.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
