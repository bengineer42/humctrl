"""`sim_humidity_chamber`: the humidity chamber as a `MultiPlant` link, no hardware."""

from __future__ import annotations

import pytest

from humidity.blender import SupplyHumiditiesError
from humidity.sim import HumidityChamber, HumidityChamberConfig


def test_it_builds_a_multi_plant_with_six_named_outputs() -> None:
    plant = HumidityChamberConfig(dry=10.0, wet=90.0, tau_s=5.0, initial=40.0, noise=0.0).build()
    assert isinstance(plant, HumidityChamber)
    assert set(plant.output_names) == {
        "chamber_humidity",
        "chamber_temperature",
        "dry_humidity",
        "dry_temperature",
        "wet_humidity",
        "wet_temperature",
    }
    assert plant.output("chamber_humidity") == pytest.approx(40.0)
    assert plant.output("dry_humidity") == pytest.approx(10.0)
    assert plant.output("wet_humidity") == pytest.approx(90.0)


def test_the_supplies_and_every_temperature_are_steady() -> None:
    plant = HumidityChamberConfig(temperature=21.5, noise=0.0).build()
    plant.inputs["wet_fraction"] = 1.0
    for t in range(0, 100_000_000_000, 1_000_000_000):
        plant.advance(t)
    assert plant.output("dry_humidity") == pytest.approx(10.0)
    assert plant.output("wet_humidity") == pytest.approx(90.0)
    for port in ("chamber_temperature", "dry_temperature", "wet_temperature"):
        assert plant.output(port) == pytest.approx(21.5)


def test_wet_fraction_0_settles_towards_dry_and_1_towards_wet() -> None:
    plant = HumidityChamberConfig(dry=10.0, wet=90.0, tau_s=1.0, initial=40.0, noise=0.0).build()
    plant.inputs["wet_fraction"] = 0.0
    for t in range(0, 50_000_000_000, 1_000_000_000):
        plant.advance(t)
    assert plant.output("chamber_humidity") == pytest.approx(10.0, abs=0.1)

    plant = HumidityChamberConfig(dry=10.0, wet=90.0, tau_s=1.0, initial=40.0, noise=0.0).build()
    plant.inputs["wet_fraction"] = 1.0
    for t in range(0, 50_000_000_000, 1_000_000_000):
        plant.advance(t)
    assert plant.output("chamber_humidity") == pytest.approx(90.0, abs=0.1)


def test_feedforward_and_inverse_feedforward_are_the_lag_s_static_inverse() -> None:
    plant = HumidityChamberConfig(dry=10.0, wet=90.0).build()
    assert plant.feedforward("wet_fraction", 50.0) == pytest.approx(0.5)
    assert plant.inverse_feedforward("wet_fraction", 0.5) == pytest.approx(50.0)
    with pytest.raises(ValueError, match="no input 'nope'"):
        plant.feedforward("nope", 50.0)


def test_noise_perturbs_only_the_chamber_reading() -> None:
    plant = HumidityChamberConfig(dry=10.0, wet=90.0, initial=50.0, noise=1.0, seed=1).build()
    readings = {round(plant.output("chamber_humidity"), 3) for _ in range(20)}
    assert len(readings) > 1
    assert {plant.output("dry_humidity") for _ in range(5)} == {10.0}


def test_wet_not_greater_than_dry_is_refused() -> None:
    with pytest.raises(SupplyHumiditiesError):
        HumidityChamberConfig(dry=90.0, wet=10.0).build()
