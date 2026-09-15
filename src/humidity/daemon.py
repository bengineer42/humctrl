"""Daemon entry point.

Builds a rig, attaches it to the HTTP server and runs in the foreground. Nothing
here forks or writes a PID file: systemd ``Type=simple`` supervises the process
directly, and :mod:`humidity.cli` drives it over the same HTTP API the browser
uses.
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import suppress

from flyball.control import PIController
from flyball.core.clock import Clock
from flyball.rig import HumRig

from humidity.pumps import DualPumps, PumpPair
from humidity.readers import HTReading

log = logging.getLogger("humidity.daemon")


def build_rig(args: argparse.Namespace) -> HumRig:
    """A rig on real hardware.

    The imports are local because they only resolve on a Pi: pulling blinka in
    at module scope would stop the daemon importing anywhere else, including in
    ``--simulate`` mode.

    Args:
        args: Parsed command line, supplying the pump, PWM and sensor settings.

    Returns:
        A manager wired to the real pumps and sensors.
    """
    from humidity.direct import I2CSHT4x, LinuxPWMPump

    pumps = DualPumps(
        PumpPair(
            wet=LinuxPWMPump(
                channel=args.wet_channel,
                frequency=args.pwm_frequency,
                deadband=args.wet_deadband,
                chip=args.pwm_chip,
            ),
            dry=LinuxPWMPump(
                channel=args.dry_channel,
                frequency=args.pwm_frequency,
                deadband=args.dry_deadband,
                chip=args.pwm_chip,
            ),
        ),
        wet_max_flow=args.wet_max_flow,
        dry_max_flow=args.dry_max_flow,
        units=args.flow_units,
    )
    sensor = I2CSHT4x(label=args.sensor_label)
    return HumRig(
        pumps=pumps,
        process_reader=sensor,
        regulator=PIController(kp=args.kp, ki=args.ki),
        process_time=args.loop_time,
    )


# region Simulation


class SimPump:
    """A pump that only remembers what it was told."""

    def __init__(self) -> None:
        self._effort = 0.0

    @property
    def effort(self) -> float:
        return self._effort

    def set_effort(self, effort: float) -> None:
        self._effort = effort

    def stop(self) -> None:
        self._effort = 0.0


class SimSensor:
    """A chamber that lazily follows the wet fraction the pumps are holding."""

    def __init__(self, label: str, pumps: PumpPair, clock: Clock | None = None) -> None:
        self._label = label
        self._pumps = pumps
        self._clock = clock or Clock()
        self._humidity = 50.0

    def read(self) -> HTReading:
        efforts = self._pumps.efforts
        total = efforts.wet + efforts.dry
        target = 100.0 * efforts.wet / total if total > 0 else 20.0
        self._humidity += 0.1 * (target - self._humidity)
        return HTReading(self._label, self._clock.now_ns(), self._humidity, 21.0)


def build_simulated_rig(args: argparse.Namespace) -> HumRig:
    pair = PumpPair(wet=SimPump(), dry=SimPump())
    pumps = DualPumps(
        pair,
        wet_max_flow=args.wet_max_flow,
        dry_max_flow=args.dry_max_flow,
        units=args.flow_units,
    )
    sensor = SimSensor(args.sensor_label, pair)
    return HumRig(
        pumps=pumps,
        process_readerr=sensor,
        sensors={args.sensor_label: sensor},
        regulator=PIController(kp=args.kp, ki=args.ki),
        process_time=args.loop_time,
    )


# endregion


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="humidity-daemon", description=__doc__)
    p.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--simulate", action="store_true", help="run against a simulated chamber")
    p.add_argument("--log-level", default="info")

    rig = p.add_argument_group("rig")
    rig.add_argument("--loop-time", type=float, default=1.0, help="seconds between control steps")
    rig.add_argument("--sensor-label", default="chamber")
    rig.add_argument("--kp", type=float, default=0.02)
    rig.add_argument("--ki", type=float, default=0.001)

    flow = p.add_argument_group("flow")
    flow.add_argument("--wet-max-flow", type=float, default=1.8)
    flow.add_argument("--dry-max-flow", type=float, default=2.0)
    flow.add_argument("--flow-units", default="L/min")

    pwm = p.add_argument_group("pwm")
    pwm.add_argument("--wet-channel", type=int, default=0)
    pwm.add_argument("--dry-channel", type=int, default=1)
    pwm.add_argument("--pwm-chip", type=int, default=0)
    pwm.add_argument("--pwm-frequency", type=float, default=25_000.0)
    pwm.add_argument("--wet-deadband", type=float, default=0.0)
    pwm.add_argument("--dry-deadband", type=float, default=0.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    try:
        manager = build_simulated_rig(args) if args.simulate else build_rig(args)
    except Exception as e:  # missing hardware, wrong channel, no I2C device
        log.error("could not build the rig: %s", e)
        return 1

    # systemd restarts us into whatever state the last process died in, and PWM
    # duty survives the process. Never inherit a running pump.
    manager.stop_pumps()

    try:
        manager.start()
    except Exception as e:
        log.error("could not start the control loop: %s", e)
        manager.stop_pumps()
        return 1

    # Import here so a rig can be built and inspected without the web extra.
    import uvicorn
    from flyball.server import create_app, set_rig

    set_rig(manager)
    log.info("serving on http://%s:%d (simulated=%s)", args.host, args.port, args.simulate)
    try:
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level=args.log_level)
    finally:
        set_rig(None)
        with suppress(Exception):
            manager.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
