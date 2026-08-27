"""Entry point: `python -m mcp_server` serves karani over stdio."""
from __future__ import annotations

import logging
import sys

from .server import app


def main() -> None:
    # stdio transport: stdout is the protocol channel, so logs go to stderr.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.run("stdio")


if __name__ == "__main__":
    main()
