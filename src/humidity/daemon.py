"""Daemon entry point: `humidity-daemon rig.yaml [sim.yaml ...]`.

Loads the rig from its file(s) -- later overlaying earlier, `--set` last --
through the generic loader (device model, controllers, links); the real
rig is `rig.yaml` alone, the simulated one `rig.yaml sim.yaml`.
`humidity.cli` drives whichever is running over the same HTTP API the
browser uses.
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import suppress

log = logging.getLogger("humidity.daemon")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="humidity-daemon", description=__doc__)
    p.add_argument("rig", nargs="+", metavar="FILE", help="rig file(s), later overlaying earlier")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", dest="sets")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db", default=None, help="record into this SQLite file")
    p.add_argument("--log-level", default="info")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    from flyball.runtime.config import load_rig_config

    try:
        rig = load_rig_config(args.rig, args.sets).build()
    except Exception as e:  # missing hardware, a bad file, an unresolved link
        log.error("could not build the rig: %s", e)
        return 1

    # systemd restarts us into whatever state the last process died in, and a
    # PWM duty survives the process: never inherit a running pump.
    blender = rig.devices.get("blender")
    if blender is not None:
        with suppress(Exception):
            blender.stop()  # type: ignore[attr-defined]

    import uvicorn
    from flyball.server import create_app, set_rig

    if args.db:
        from flyball.db.sqlite import SqliteStore

        rig.start_recording(SqliteStore(args.db))
    set_rig(rig)
    log.info("serving on http://%s:%d", args.host, args.port)
    try:
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    finally:
        set_rig(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
