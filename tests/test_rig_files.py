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

    # `blender` is the *real* `dual_pump_blender` driver in both files -- same class, so
    # every signal, unit, access, limits and command matches exactly, not just a subset.
    assert type(sim_blender) is DualPumpBlender
    assert set(sim_blender.signals) == set(real_blender.signals)
    for path, signal in real_blender.signals.items():
        mirrored = sim_blender.signals[path]
        assert mirrored.address == f"blender.{path}"
        assert mirrored.unit is signal.unit
        assert mirrored.access == signal.access
        assert mirrored.limits == signal.limits
    assert set(sim_blender.commands) == set(real_blender.commands) == {"stop"}


def test_the_default_controller_settles_the_chamber_towards_its_reference() -> None:
    clock = SteppedClock(0)
    # The chamber's own drift/noise is zeroed here for a deterministic settle -- it feeds
    # the real blender's `bound` supply readings, same as a real sensor's drift would.
    rig = load_rig_config(
        [ROOT / "rig.yaml", ROOT / "sim.yaml"],
        sets=[
            "links.chamber.supply_drift_rh=0",
            "links.chamber.supply_noise_rh=0",
            "links.chamber.noise_rh=0",
        ],
    ).build(clock=clock)
    controller = rig.controllers.resolve(None)
    assert controller is not None
    controller.regulate(60.0, transfer=Transfer.RESET)
    clock.advance(600.0)  # volume_l 3, up to 2 L/min a line: tau well under a minute
    chamber_humidity = rig.devices["hum_sensors"].signals["chamber.humidity"]
    assert rig.read(chamber_humidity).value == pytest.approx(60.0, abs=2.0)


def test_manual_flows_through_the_real_blender_settle_the_chamber_and_its_own_readback() -> None:
    """`programs/demo.yaml`'s first step (`set: {dry_flow: 2, wet_flow: 2}`), run for real."""
    clock = SteppedClock(0)
    rig = load_rig_config(
        [ROOT / "rig.yaml", ROOT / "sim.yaml"],
        sets=[
            "links.chamber.supply_drift_rh=0",
            "links.chamber.supply_noise_rh=0",
            "links.chamber.noise_rh=0",
        ],
    ).build(clock=clock)
    blender = rig.devices["blender"]
    rig.demand(blender.root, {"dry_flow": 2.0, "wet_flow": 2.0})
    clock.advance(300.0)
    chamber_humidity = rig.devices["hum_sensors"].signals["chamber.humidity"]
    # Equal flows on equal-maximum lines: the midpoint of the (bound) supplies' humidity.
    assert rig.read(chamber_humidity).value == pytest.approx(50.0, abs=2.0)
    expected = rig.read(blender.signals["expected_humidity"]).value
    assert expected == pytest.approx(50.0, abs=0.5)  # the blender's own readback agrees
