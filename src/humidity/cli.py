"""Command line client for a running `humidity-runner`, over its HTTP API.

Imports nothing from the domain: the wire format is the contract (plan
`DEVICE-MODEL-PLAN.md` §4). Addresses are whatever the running rig file
declares -- `blender.humidity`, `hum_sensors.chamber.humidity` for
`rig.yaml`; a `--set`-only rig may use others.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8000"

EXIT_ERROR = 1
EXIT_UNREACHABLE = 3


class CliError(Exception):
    """Anything the user should see as a one-line message, not a traceback."""

    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code


def request(args: argparse.Namespace, method: str, path: str, body: Any = None) -> Any:
    try:
        response = httpx.request(method, f"{args.url}{path}", json=body, timeout=args.timeout)
    except httpx.ConnectError:
        raise CliError(
            f"no runner at {args.url} (is humidity.service running?)", EXIT_UNREACHABLE
        ) from None
    except httpx.HTTPError as e:
        raise CliError(f"{type(e).__name__}: {e}", EXIT_UNREACHABLE) from e

    if response.status_code >= 400:
        raise CliError(detail(response))
    if not response.content:
        return None
    return response.json()


def detail(response: httpx.Response) -> str:
    """The server's own explanation, if it gave one."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text.strip()}"
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return f"HTTP {response.status_code}: {payload}"


def emit(args: argparse.Namespace, value: Any) -> None:
    if args.json:
        print(json.dumps(value, indent=2))
    elif isinstance(value, dict):
        print(flatten(value))
    elif value is not None:
        print(value)


def flatten(value: dict[str, Any], prefix: str = "") -> str:
    """Nested dicts as indented `key: value` lines. Lists stay compact."""
    lines = []
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, dict):
            lines.append(f"{name}:")
            lines.append(flatten(item, prefix=f"{prefix}  "))
        else:
            lines.append(f"{name}: {item}")
    return "\n".join(lines)


def parse_values(pairs: list[str]) -> dict[str, float]:
    """`["flows.dry=0.4", "flows.wet=0.6"]` -> `{"flows.dry": 0.4, "flows.wet": 0.6}`."""
    values: dict[str, float] = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep:
            raise CliError(f"{pair!r}: expected NAME=VALUE")
        try:
            values[name] = float(raw)
        except ValueError:
            raise CliError(f"{pair!r}: {raw!r} is not a number") from None
    return values


# region Commands


def cmd_status(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/devices"))


def cmd_health(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/health"))


def cmd_device(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", f"/api/devices/{args.device}"))


def cmd_read(args: argparse.Namespace) -> None:
    path = f"/api/read/{args.address}"
    emit(args, request(args, "GET", f"{path}?fresh=true" if args.fresh else path))


def cmd_set(args: argparse.Namespace) -> None:
    values = parse_values(args.values)
    emit(args, request(args, "PUT", f"/api/devices/{args.device}/demand", values))


def cmd_command(args: argparse.Namespace) -> None:
    body = parse_values(args.args) if args.args else None
    emit(args, request(args, "POST", f"/api/devices/{args.device}/commands/{args.name}", body))


def cmd_stop(args: argparse.Namespace) -> None:
    emit(args, request(args, "POST", "/api/devices/blender/commands/stop"))


def cmd_controller(args: argparse.Namespace) -> None:
    if args.at is None:
        emit(args, request(args, "GET", f"/api/controllers/{args.address}"))
        return
    emit(args, request(args, "PUT", f"/api/controllers/{args.address}/reference", {"at": args.at}))


def format_frame(frame: Any, raw: bool = False) -> str:
    """One sample per line, falling back to JSON for anything unexpected."""
    if raw or not isinstance(frame, dict):
        return json.dumps(frame)
    return json.dumps(frame)


def cmd_watch(args: argparse.Namespace) -> None:
    """Follow the sample stream until interrupted."""
    try:
        import websockets
    except ImportError:
        raise CliError("watch needs the 'websockets' package") from None

    url = args.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)

    async def follow() -> None:
        async with websockets.connect(f"{url}/ws/samples") as socket:
            async for frame in socket:
                print(format_frame(json.loads(frame), raw=args.json))
                sys.stdout.flush()

    try:
        asyncio.run(follow())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        raise CliError(f"could not stream from {url}: {e}", EXIT_UNREACHABLE) from e


# endregion


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="humidity", description="Control a running humidity runner.")
    p.add_argument(
        "--url",
        default=os.environ.get("HUMIDITY_URL", DEFAULT_URL),
        help=f"runner base URL (env HUMIDITY_URL, default {DEFAULT_URL})",
    )
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--json", action="store_true", help="raw JSON, for scripting")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="every device, briefly").set_defaults(fn=cmd_status)
    sub.add_parser("health", help="is a rig attached").set_defaults(fn=cmd_health)
    sub.add_parser("stop", help="stop the blender's pumps").set_defaults(fn=cmd_stop)
    sub.add_parser("watch", help="follow samples as they arrive").set_defaults(fn=cmd_watch)

    device = sub.add_parser("device", help="one device's config, settings and state")
    device.add_argument("device")
    device.set_defaults(fn=cmd_device)

    read = sub.add_parser("read", help="the last (or, with --fresh, a new) reading on an address")
    read.add_argument("address", help="e.g. hum_sensors.chamber.humidity")
    read.add_argument("--fresh", action="store_true")
    read.set_defaults(fn=cmd_read)

    set_ = sub.add_parser("set", help="demand one or more signals on a device")
    set_.add_argument("device", help="e.g. blender")
    set_.add_argument("values", nargs="+", metavar="NAME=VALUE", help="relative to the device")
    set_.set_defaults(fn=cmd_set)

    command = sub.add_parser("command", help="run a device's command")
    command.add_argument("device")
    command.add_argument("name")
    command.add_argument("args", nargs="*", metavar="NAME=VALUE")
    command.set_defaults(fn=cmd_command)

    controller = sub.add_parser("controller", help="read or set a controller's reference")
    controller.add_argument("address", help="the target signal's address, e.g. blender.humidity")
    controller.add_argument("--at", type=float, help="a new reference; omit to read")
    controller.set_defaults(fn=cmd_controller)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.fn(args)
    except CliError as e:
        print(f"humidity: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
