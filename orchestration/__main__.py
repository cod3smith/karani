"""Entry: `python -m orchestration --once | --loop [seconds] | --show`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser("orchestration")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true",
                      help="run one hunt pass and exit")
    mode.add_argument("--loop", type=int, nargs="?", const=3600,
                      metavar="SECONDS",
                      help="run continuously (default every 3600s)")
    mode.add_argument("--show", action="store_true",
                      help="print the graph as mermaid")
    args = parser.parse_args()

    try:
        from .graph import build_hunt_graph, run_hunt_once  # noqa: F401
    except ImportError:
        print("langgraph not installed. Run `uv sync --extra orchestrator`.",
              file=sys.stderr)
        sys.exit(3)

    if args.show:
        from .graph import HuntDeps
        graph = build_hunt_graph(HuntDeps(
            storage=None, make_qualifier=lambda: None,
            load_resume=lambda: None,
        ))
        print(graph.get_graph().draw_mermaid())
        return

    async def _loop(interval: int) -> None:
        while True:
            state = await run_hunt_once()
            errors = state.get("errors", [])
            print(f"hunt pass done: errors={len(errors)}")
            await asyncio.sleep(interval)

    if args.once:
        state = asyncio.run(run_hunt_once())
        errors = state.get("errors", [])
        print(f"ingest={state.get('ingest')}")
        print(f"qualify={state.get('qualify')}")
        print(f"autopilot={state.get('autopilot')}")
        print(f"notion={state.get('notion')}")
        if errors:
            for e in errors:
                print(f"ERR: {e}", file=sys.stderr)
        sys.exit(1 if errors else 0)
    else:
        try:
            asyncio.run(_loop(args.loop))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
