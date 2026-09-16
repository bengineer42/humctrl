"""`rig.yaml` (the real rig), and `rig.yaml` + `sim.yaml` (the overlay), actually load and build.

`rig.yaml` alone is not *built* here: its links are real hardware (Blinka
I2C, a PWM chip) this suite does not have. `humidity` is imported so its
drivers, links and plant are registered without relying on the
`flyball.configs` entry point having been (re)installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flyball.control import Transfer
from flyball.runtime.config import load_rig_config
from flyball.sim import SteppedClock

import humidity  # ruff: ignore[unused-import]
from humidity.blender import DualPumpBlender
from humidity.pumps import DualPumps, MaxFlows, PumpPair, SupplyHumidities

ROOT = Path(__file__).resolve().parents[1]


def test_rig_yaml_validates() -> None:
    config = load_rig_config(ROOT / "rig.yaml")
    assert set(config.devices) == {"hum_sensors", "blender"}
    assert config.devices["hum_sensors"].driver == "sht4x_set"
    assert config.devices["blender"].driver == "dual_pump_blender"
    assert "blender.humidity" in config.controllers


def test_rig_yaml_overlaid_with_sim_yaml_builds_headless() -> None:
    rig = load_rig_config([ROOT / "rig.yaml", ROOT / "sim.yaml"]).build(start=False)
    assert set(rig.devices) == {"hum_sensors", "blender"}
    sensors, blender = rig.devices["hum_sensors"], rig.devices["blender"]
    assert rig.resolve("hum_sensors.chamber.humidity") is sensors.signals["chamber.humidity"]
    assert rig.resolve("blender.humidity") is blender.signals["humidity"]
    default = rig.controllers.resolve(None)
    assert default is not None and default.name == "blender.humidity"


class _FakeI2C:
    """Just enough of `I2CBus` for `Sht4xSet.build()` -- no read is ever asked of it here."""

    def try_lock(self) -> bool:
        return True

    def unlock(self) -> None:
        pass

    def writeto(self, address: int, buffer: bytes) -> None:
        pass

    def readfrom_into(self, address: int, buffer: bytearray) -> None:
        pass


class _FakePump:
    def __init__(self) -> None:
        self._effort = 0.0

    @property
    def effort(self) -> float:
        return self._effort

    def set_effort(self, effort: float) -> float:
        self._effort = effort
        return effort

    def stop(self) -> None:
        self._effort = 0.0


def _real_blender() -> DualPumpBlender:
    """`blender` as `rig.yaml` declares it, built directly: its pumps need real PWM hardware."""
    pumps = DualPumps(PumpPair(_FakePump(), _FakePump()), MaxFlows(dry=2.0, wet=2.0))
    return DualPumpBlender("blender", pumps, supply=SupplyHumidities(dry=10.0, wet=90.0))


def test_the_overlay_mirrors_every_signal_rig_yaml_and_sim_yaml_both_declare() -> None:
    real_entry = load_rig_config(ROOT / "rig.yaml").devices["hum_sensors"]
    real_sensors = real_entry.build("hum_sensors", links={"i2c1": _FakeI2C()})
    real_blender = _real_blender()

    sim = load_rig_config([ROOT / "rig.yaml", ROOT / "sim.yaml"]).build(start=False)
    sim_sensors, sim_blender = sim.devices["hum_sensors"], sim.devices["blender"]

    assert set(real_sensors.signals) <= set(sim_sensors.signals), (
        "sim.yaml must declare every signal the real hum_sensors does"
    )
    for path, signal in real_sensors.signals.items():
        mirrored = sim_sensors.signals[path]
        assert mirrored.address == f"hum_sensors.{path}"
        assert mirrored.unit is signal.unit
        assert mirrored.access == signal.access

    # The sim's blender is reduced to `humidity` alone (plan overlay note); that one signal
    # matches the real one exactly.
    assert set(sim_blender.signals) == {"humidity"}
    real_humidity, mirrored = real_blender.signals["humidity"], sim_blender.signals["humidity"]
    assert mirrored.address == "blender.humidity"
    assert mirrored.unit is real_humidity.unit
    assert mirrored.access == real_humidity.access
    assert mirrored.limits == real_humidity.limits


def test_the_default_controller_settles_the_chamber_towards_its_reference() -> None:
    clock = SteppedClock(0)
    rig = load_rig_config([ROOT / "rig.yaml", ROOT / "sim.yaml"]).build(clock=clock)
    controller = rig.controllers.resolve(None)
    assert controller is not None
    controller.regulate(60.0, transfer=Transfer.RESET)
    clock.advance(600.0)  # tau_s 45 s: over 13 time constants
    chamber_humidity = rig.devices["hum_sensors"].signals["chamber.humidity"]
    assert rig.read(chamber_humidity).value == pytest.approx(60.0, abs=2.0)
