"""Command line client.

Talks to a running ``humidity-daemon`` over the same HTTP API the browser uses,
so it works against a rig on another machine and needs none of the hardware
libraries. It deliberately imports nothing from the domain: the wire format is
the contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
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
            f"no daemon at {args.url} (is humidity.service running?)", EXIT_UNREACHABLE
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
    """Nested dicts as indented ``key: value`` lines. Lists stay compact."""
    lines = []
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, dict):
            lines.append(f"{name}:")
            lines.append(flatten(item, prefix=f"{prefix}  "))
        else:
            lines.append(f"{name}: {item}")
    return "\n".join(lines)


# region Commands


def cmd_status(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/state"))


def cmd_health(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/health"))


def cmd_reading(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/process_reading"))


def cmd_target(args: argparse.Namespace) -> None:
    if args.humidity is None:
        emit(args, request(args, "GET", "/api/regulation"))
        return
    request(args, "PUT", "/api/regulation", {"humidity": args.humidity})
    emit(args, request(args, "GET", "/api/state"))


def cmd_blend(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"flow": args.flow, "wet_fraction": args.wet_fraction}
    if args.on_overdrive:
        body["on_overdrive"] = args.on_overdrive
    emit(args, request(args, "PUT", "/api/pumps/blend", body))


def cmd_flow(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"flow": args.flow}
    if args.on_overdrive:
        body["on_overdrive"] = args.on_overdrive
    emit(args, request(args, "PUT", "/api/pumps/flow", body))


def cmd_fraction(args: argparse.Namespace) -> None:
    line = "dry_fraction" if args.dry else "wet_fraction"
    if args.value is None:
        emit(args, request(args, "GET", f"/api/pumps/{line}"))
        return
    body: dict[str, Any] = {"fraction": args.value}
    if args.policy:
        body["policy"] = args.policy
    emit(args, request(args, "PUT", f"/api/pumps/{line}", body))


def cmd_flows(args: argparse.Namespace) -> None:
    if args.wet is None:
        emit(args, request(args, "GET", "/api/pumps/flows"))
        return
    request(args, "PUT", "/api/pumps/flows", {"wet": args.wet, "dry": args.dry})
    emit(args, request(args, "GET", "/api/pumps/flows"))


def cmd_efforts(args: argparse.Namespace) -> None:
    if args.wet is None:
        emit(args, request(args, "GET", "/api/pumps/efforts"))
        return
    request(args, "PUT", "/api/pumps/efforts", {"wet": args.wet, "dry": args.dry})
    emit(args, request(args, "GET", "/api/pumps/efforts"))


def cmd_pumps(args: argparse.Namespace) -> None:
    emit(args, request(args, "GET", "/api/pumps/"))


def cmd_stop(args: argparse.Namespace) -> None:
    emit(args, request(args, "POST", "/api/pumps/stop"))


def format_frame(frame: Any, raw: bool = False) -> str:
    """One reading per line, falling back to JSON for anything unexpected."""
    if raw or not isinstance(frame, dict):
        return json.dumps(frame)
    try:
        clock = datetime.fromtimestamp(frame["time_ns"] / 1e9).strftime("%H:%M:%S.%f")[:-3]
        return f"{clock}  {frame['humidity']:>6.2f} %RH  {frame['temperature']:>6.2f} C"
    except (KeyError, TypeError, ValueError):
        return json.dumps(frame)


def cmd_watch(args: argparse.Namespace) -> None:
    """Follow the reading stream until interrupted."""
    try:
        import websockets
    except ImportError:
        raise CliError("watch needs the 'websockets' package") from None

    url = args.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)

    async def follow() -> None:
        async with websockets.connect(f"{url}/ws/process_readings") as socket:
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
    p = argparse.ArgumentParser(prog="humidity", description="Control a running humidity daemon.")
    p.add_argument(
        "--url",
        default=os.environ.get("HUMIDITY_URL", DEFAULT_URL),
        help=f"daemon base URL (env HUMIDITY_URL, default {DEFAULT_URL})",
    )
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--json", action="store_true", help="raw JSON, for scripting")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="the whole rig snapshot").set_defaults(fn=cmd_status)
    sub.add_parser("health", help="is a rig attached").set_defaults(fn=cmd_health)
    sub.add_parser("reading", help="the last process reading").set_defaults(fn=cmd_reading)
    sub.add_parser("pumps", help="pump state").set_defaults(fn=cmd_pumps)
    sub.add_parser("stop", help="stop the pumps and regulation").set_defaults(fn=cmd_stop)
    sub.add_parser("watch", help="follow readings as they arrive").set_defaults(fn=cmd_watch)

    target = sub.add_parser("target", help="read or set the regulated humidity")
    target.add_argument("humidity", nargs="?", type=float, help="%%RH; omit to read")
    target.set_defaults(fn=cmd_target)

    blend = sub.add_parser("blend", help="set total flow and blend ratio together")
    blend.add_argument("flow", type=float)
    blend.add_argument("wet_fraction", type=float)
    blend.add_argument("--on-overdrive", choices=["clamp", "raise"])
    blend.set_defaults(fn=cmd_blend)

    flow = sub.add_parser("flow", help="set total flow, holding the blend ratio")
    flow.add_argument("flow", type=float)
    flow.add_argument("--on-overdrive", choices=["clamp", "raise"])
    flow.set_defaults(fn=cmd_flow)

    fraction = sub.add_parser("fraction", help="read or set the blend ratio")
    fraction.add_argument("value", nargs="?", type=float, help="0-1; omit to read")
    fraction.add_argument("--dry", action="store_true", help="the dry line instead of the wet")
    fraction.add_argument("--policy", choices=["hold_clamped", "hold_flow", "hold_effort"])
    fraction.set_defaults(fn=cmd_fraction)

    flows = sub.add_parser("flows", help="read or set each line in absolute flow")
    flows.add_argument("wet", nargs="?", type=float)
    flows.add_argument("dry", nargs="?", type=float)
    flows.set_defaults(fn=cmd_flows)

    efforts = sub.add_parser("efforts", help="read or set each line in 0-1 effort")
    efforts.add_argument("wet", nargs="?", type=float)
    efforts.add_argument("dry", nargs="?", type=float)
    efforts.set_defaults(fn=cmd_efforts)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command in {"flows", "efforts"} and (args.wet is None) != (args.dry is None):
        print(f"humidity: {args.command} takes both values or neither", file=sys.stderr)
        return EXIT_ERROR
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
