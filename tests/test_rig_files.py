"""`rig.yaml` (the real rig), and `rig.yaml` + `sim.yaml` (the overlay), actually load and build.

`rig.yaml` alone is not *built* here: `i2c` needs a real bus and `pwm` a
real chip's sysfs tree, neither of which this suite has. `humidity` and
`flyball_linux` are imported so their drivers, links and plant are
registered without relying on the `flyball.configs` entry point having
been (re)installed.
"""

from __future__ import annotations

from pathlib import Path

import flyball_linux.configs  # ruff: ignore[unused-import]
import pytest
from flyball.control import Transfer
from flyball.runtime.config import load_rig_config
from flyball.sim import SteppedClock
from flyball_linux.links.i2c import FakeI2c
from flyball_linux.links.pwm import FakePwm

import humidity  # ruff: ignore[unused-import]
from humidity.blender import DualPumpBlender, DualPumpBlenderConfig, PumpLineConfig, SupplyConfig

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


def _real_blender() -> DualPumpBlender:
    """`blender` as `rig.yaml` declares it, built through its real config on a `fake_pwm` chip."""
    config = DualPumpBlenderConfig(
        link="pwm0",
        dry=PumpLineConfig(channel=0, deadband=0.05, max_flow=2.0),
        wet=PumpLineConfig(channel=1, deadband=0.05, max_flow=2.0),
        supply=SupplyConfig(dry=10.0, wet=90.0),
    )
    # As `DeviceEntry.build` substitutes a link name for the built object: `model_copy`
    # bypasses validation, since `FakePwm` is not one of `PwmLinkConfig`'s tagged members.
    return config.model_copy(update={"link": FakePwm()}).build("blender")


def test_the_overlay_mirrors_every_signal_rig_yaml_and_sim_yaml_both_declare() -> None:
    real_entry = load_rig_config(ROOT / "rig.yaml").devices["hum_sensors"]
    real_sensors = real_entry.build("hum_sensors", links={"i2c1": FakeI2c()})
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
