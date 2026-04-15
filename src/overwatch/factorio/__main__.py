from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from overwatch.config import Settings
from overwatch.factorio.agent import run_factorio_agent
from overwatch.factorio.executor import SkillExecutor
from overwatch.factorio.session import FactorioSessionStore
from overwatch.factorio.tech_tree import load_tech_tree_text


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Factorio closed-loop research agent (lab only). Ctrl+C stops between steps.",
    )
    ap.add_argument("--goal", required=True, help="High-level objective for the planner")
    ap.add_argument("--max-steps", type=int, default=5, ge=1, le=10_000)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Send real key/mouse events via pyautogui (default: dry-run only)",
    )
    ap.add_argument(
        "--no-click",
        action="store_true",
        help="With --execute: keys/skills only; ignore planner click actions (safer smoke test)",
    )
    ap.add_argument(
        "--tech-tree",
        type=Path,
        default=None,
        help="Path to tech/milestone JSON (overrides FACTORIO_TECH_TREE_PATH)",
    )
    ap.add_argument("--monitor", type=int, default=1, ge=0, help="mss monitor index (1 = primary)")
    ap.add_argument(
        "--settle",
        type=float,
        default=None,
        help="Seconds to wait after each action (default: FACTORIO_SETTLE_SEC)",
    )
    ap.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Reuse an existing session id (must exist in the local factorio store)",
    )
    return ap


async def _async_main(settings: Settings, args: argparse.Namespace) -> int:
    store = FactorioSessionStore(settings.factorio_data_root)
    try:
        if args.session_id:
            if not store.has_session(args.session_id):
                logging.error("Unknown session id: %s", args.session_id)
                return 2
            session_id = args.session_id
        else:
            session_id = store.create_session(meta={"goal": args.goal})

        tt_path = args.tech_tree if args.tech_tree is not None else settings.factorio_tech_tree_path
        tech_text = load_tech_tree_text(tt_path)

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                continue

        ex = SkillExecutor(
            max_actions_per_minute=settings.factorio_max_actions_per_minute,
            dry_run=not args.execute,
            allow_click=not args.no_click,
        )
        n = await run_factorio_agent(
            settings,
            store,
            session_id,
            goal=args.goal,
            tech_tree_text=tech_text,
            executor=ex,
            max_steps=args.max_steps,
            settle_sec=args.settle,
            monitor=args.monitor,
            stop_event=stop_event,
        )
        logging.info("Completed %s step(s); session_id=%s", n, session_id)
        return 0
    finally:
        store.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings()
    args = _build_arg_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_async_main(settings, args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
