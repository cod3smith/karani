"""Entry point: `python -m slackbridge` runs the Socket Mode listener."""
from __future__ import annotations

import asyncio
import logging
import sys

from .listener import run_listener


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
