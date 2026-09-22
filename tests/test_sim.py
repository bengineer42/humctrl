"""`sim_humidity_chamber`: a mixing-model chamber that is also a fake PWM chip."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from flyball_sim.plant import MultiPlant
from flyball_linux.links.pwm import PwmLink

from humidity.blender import SupplyHumiditiesError
from humidity.sim import DRY_CHANNEL, WET_CHANNEL, HumidityChamber, HumidityChamberConfig

PERIOD_NS = 1_000_000  # arbitrary; only the duty:period ratio matters


def _drive(plant: HumidityChamber, channel: int, effort: float) -> None:
    """Command `channel` to `effort` (0..1), exactly as `PwmPump.set_effort` would."""
    plant.configure(channel, PERIOD_NS, round(effort * PERIOD_NS))
    plant.enable(channel, effort > 0.0)


def _settle(plant: HumidityChamber, seconds: float) -> None:
    plant.advance(0)  # baseline: the first call never steps
    plant.advance(round(seconds * 1e9))


def test_it_builds_a_multi_plant_and_a_pwm_link_with_six_named_outputs() -> None:
    plant = HumidityChamberConfig().build()
    assert isinstance(plant, HumidityChamber)
    assert isinstance(plant, MultiPlant)
    assert isinstance(plant, PwmLink)
    assert set(plant.output_names) == {
        "chamber_humidity",
        "chamber_temperature",
        "dry_humidity",
        "dry_temperature",
        "wet_humidity",
        "wet_temperature",
    }


def test_wet_not_greater_than_dry_is_refused() -> None:
    with pytest.raises(SupplyHumiditiesError):
        HumidityChamberConfig(dry_rh=90.0, wet_rh=10.0).build()


def test_settled_humidity_at_full_dry_full_wet_and_both_matches_the_mixing_arithmetic() -> None:
    common: dict[str, Any] = dict(
        dry_rh=10.0,
        wet_rh=90.0,
        dry_flow_l_per_min=2.0,
        wet_flow_l_per_min=2.0,
        volume_l=1.0,
        initial_rh=10.0,
        exchange_per_min=0.0,
        sensor_tau_s=0.05,
        noise_rh=0.0,
        supply_noise_rh=0.0,
        supply_drift_rh=0.0,
        temperature_noise_c=0.0,
        temperature_drift_c=0.0,
    )

    dry_only = HumidityChamberConfig(**common).build()
    _drive(dry_only, DRY_CHANNEL, 1.0)
    _settle(dry_only, 300.0)
    assert dry_only.output("chamber_humidity") == pytest.approx(10.0, abs=0.1)

    wet_only = HumidityChamberConfig(**common).build()
    _drive(wet_only, WET_CHANNEL, 1.0)
    _settle(wet_only, 300.0)
    assert wet_only.output("chamber_humidity") == pytest.approx(90.0, abs=0.1)

    both = HumidityChamberConfig(**common).build()
    _drive(both, DRY_CHANNEL, 1.0)
    _drive(both, WET_CHANNEL, 1.0)
    _settle(both, 300.0)
    # equal flows, equal maxima: the midpoint of the two supplies.
    assert both.output("chamber_humidity") == pytest.approx(50.0, abs=0.1)


def test_a_bigger_chamber_is_slower_to_settle_time_to_63pc_scales_with_v_over_q() -> None:
    common: dict[str, Any] = dict(
        dry_rh=10.0,
        wet_rh=90.0,
        dry_flow_l_per_min=2.0,
        wet_flow_l_per_min=2.0,
        initial_rh=10.0,
        exchange_per_min=0.0,
        sensor_tau_s=0.05,
        noise_rh=0.0,
        supply_noise_rh=0.0,
        supply_drift_rh=0.0,
        temperature_noise_c=0.0,
        temperature_drift_c=0.0,
    )
    # wet_flow_l_per_min == dry_flow_l_per_min == 2.0, driven on the wet channel alone:
    # tau = V / Q. V = 2 L, Q = 2 L/min -> tau = 60 s; double the volume, double tau.
    small = HumidityChamberConfig(volume_l=2.0, **common).build()
    big = HumidityChamberConfig(volume_l=4.0, **common).build()
    for plant in (small, big):
        _drive(plant, WET_CHANNEL, 1.0)
        _settle(plant, 60.0)  # exactly `small`'s tau
    small_fraction = (small.output("chamber_humidity") - 10.0) / 80.0
    big_fraction = (big.output("chamber_humidity") - 10.0) / 80.0
    assert small_fraction == pytest.approx(1 - math.exp(-1), abs=0.02)  # ~63.2%
    assert big_fraction == pytest.approx(1 - math.exp(-0.5), abs=0.02)  # ~39.3%: half as far
    assert small_fraction > big_fraction


def test_exchange_pulls_towards_ambient_with_no_flow() -> None:
    plant = HumidityChamberConfig(
        initial_rh=40.0,
        ambient_rh=45.0,
        exchange_per_min=1.0,  # tau = 1/exchange = 1 min = 60 s
        sensor_tau_s=0.05,
        noise_rh=0.0,
        supply_noise_rh=0.0,
        supply_drift_rh=0.0,
        temperature_noise_c=0.0,
        temperature_drift_c=0.0,
    ).build()
    _settle(plant, 60.0)  # one time constant, no pumps ever driven
    assert plant.output("chamber_humidity") == pytest.approx(
        45.0 - (45.0 - 40.0) * math.exp(-1), abs=0.1
    )


def test_feedforward_and_inverse_feedforward_are_the_static_mixing_inverse() -> None:
    plant = HumidityChamberConfig(
        dry_rh=10.0, wet_rh=90.0, flow_l_per_min=1.0, exchange_per_min=0.0
    ).build()
    assert plant.feedforward("wet_fraction", 50.0) == pytest.approx(0.5)
    assert plant.inverse_feedforward("wet_fraction", 0.5) == pytest.approx(50.0)
    with pytest.raises(ValueError, match="no input 'nope'"):
        plant.feedforward("nope", 50.0)


def test_feedforward_accounts_for_exchange_towards_ambient() -> None:
    plant = HumidityChamberConfig(
        dry_rh=10.0,
        wet_rh=90.0,
        flow_l_per_min=1.0,
        volume_l=1.0,
        exchange_per_min=1.0,
        ambient_rh=50.0,
    ).build()
    # At wet_fraction 1 (h_in = 90), exchange (rate 1) drags the steady state
    # halfway to ambient (50), since flow and exchange*volume are equal: 70.
    assert plant.inverse_feedforward("wet_fraction", 1.0) == pytest.approx(70.0)
    assert plant.feedforward("wet_fraction", 70.0) == pytest.approx(1.0)


def test_supply_noise_perturbs_only_the_dry_and_wet_readings() -> None:
    plant = HumidityChamberConfig(
        dry_rh=10.0, wet_rh=90.0, supply_noise_rh=1.0, supply_drift_rh=0.0, seed=1
    ).build()
    plant.advance(0)
    readings = {round(plant.output("dry_humidity"), 3) for _ in range(20)}
    assert len(readings) > 1


def test_supply_drift_wanders_the_dry_and_wet_readings_out_of_phase() -> None:
    plant = HumidityChamberConfig(
        dry_rh=10.0,
        wet_rh=90.0,
        supply_drift_rh=5.0,
        supply_drift_period_s=100.0,
        supply_noise_rh=0.0,
    ).build()
    plant.advance(0)
    assert plant.output("dry_humidity") == pytest.approx(10.0)
    assert plant.output("wet_humidity") == pytest.approx(95.0)  # a quarter-cycle ahead
    plant.advance(25_000_000_000)  # a quarter of the period
    assert plant.output("dry_humidity") == pytest.approx(15.0)
    assert plant.output("wet_humidity") == pytest.approx(90.0)


def test_temperatures_drift_around_the_baseline_and_the_chamber_warms_with_flow() -> None:
    common: dict[str, Any] = dict(
        temperature_c=20.0,
        temperature_drift_c=1.0,
        temperature_noise_c=0.0,
        dry_flow_l_per_min=2.0,
        wet_flow_l_per_min=2.0,
        flow_warming_c_per_lpm=2.0,
        noise_rh=0.0,
        supply_noise_rh=0.0,
        supply_drift_rh=0.0,
    )
    idle = HumidityChamberConfig(**common).build()
    idle.advance(0)
    expected_dry = 20.0 + 1.0 * math.sin(math.pi)  # dry's phase at t=0
    expected_chamber = 20.0 + 1.0 * math.sin(0.25 * math.pi)  # chamber's phase at t=0
    assert idle.output("dry_temperature") == pytest.approx(expected_dry)
    assert idle.output("chamber_temperature") == pytest.approx(expected_chamber)

    driven = HumidityChamberConfig(**common).build()
    driven.advance(0)
    _drive(driven, DRY_CHANNEL, 1.0)
    _drive(driven, WET_CHANNEL, 1.0)
    # warming reads off the *commanded* flow directly; no advance needed.
    assert driven.output("chamber_temperature") == pytest.approx(
        expected_chamber + 2.0 * 4.0  # flow_warming_c_per_lpm * (dry + wet) flow
    )
    assert driven.output("dry_temperature") == pytest.approx(expected_dry)  # unaffected


def test_dead_time_delays_the_mixing_effect_not_the_reading() -> None:
    """A `configure`/`enable` change must sit in the pipe for `dead_time_s` before the
    mixing equation sees it -- unlike `sensor_tau_s`, which lags the reading of an
    already-arrived change. Drive a step at t=0 with `dead_time_s=1.0`: the humidity must
    not move at all before t=1.0, then must be clearly moving once t is past it."""
    common: dict[str, Any] = dict(
        dry_rh=10.0,
        wet_rh=90.0,
        dry_flow_l_per_min=2.0,
        wet_flow_l_per_min=2.0,
        volume_l=1.0,
        initial_rh=10.0,
        exchange_per_min=0.0,
        sensor_tau_s=0.05,
        dead_time_s=1.0,
        noise_rh=0.0,
        supply_noise_rh=0.0,
        supply_drift_rh=0.0,
        temperature_noise_c=0.0,
        temperature_drift_c=0.0,
    )
    plant = HumidityChamberConfig(**common).build()
    plant.advance(0)
    h0 = plant.output("chamber_humidity")
    _drive(plant, WET_CHANNEL, 1.0)  # commanded now; must not reach the mix until t=1.0

    plant.advance(round(0.95 * 1e9))
    assert plant.output("chamber_humidity") == pytest.approx(h0, abs=1e-9)  # still nothing

    plant.advance(round(1.5 * 1e9))
    assert plant.output("chamber_humidity") > h0 + 1.0  # now clearly moving


def test_dead_time_zero_is_the_default_and_behaves_as_before() -> None:
    assert HumidityChamberConfig().dead_time_s == 0.0


def test_retune_changes_a_parameter_on_a_running_chamber_without_resetting_its_state() -> None:
    plant = HumidityChamberConfig(sensor_tau_s=0.05, noise_rh=0.0).build()
    _drive(plant, WET_CHANNEL, 0.5)
    _settle(plant, 5.0)  # mid-integration: humidity has moved off initial_rh
    h_before = plant.output("chamber_humidity")

    updated = HumidityChamberConfig(
        sensor_tau_s=0.05, noise_rh=0.0, dry_flow_l_per_min=9.0, dead_time_s=2.0
    )
    updated.retune(plant)

    # the new parameters took:
    assert plant._dry_flow_l_per_min == pytest.approx(9.0)
    assert plant._dead_time_s == pytest.approx(2.0)
    # ...but the chamber's own state -- humidity, and the live PWM drive -- did not reset:
    assert plant.output("chamber_humidity") == pytest.approx(h_before)
    assert plant._duty[WET_CHANNEL] == pytest.approx(0.5)
    assert plant._enabled[WET_CHANNEL] is True


def test_retune_refuses_a_plant_it_did_not_build() -> None:
    with pytest.raises(ValueError, match="not a HumidityChamber"):
        HumidityChamberConfig().retune(object())


def test_retune_refuses_wet_rh_not_greater_than_dry_rh() -> None:
    plant = HumidityChamberConfig(dry_rh=10.0, wet_rh=90.0).build()
    with pytest.raises(ValueError, match="blend direction"):
        HumidityChamberConfig(dry_rh=90.0, wet_rh=10.0).retune(plant)


def test_the_chamber_counts_as_a_simulated_plant_in_simulation_plants() -> None:
    """The essential wiring for `sim_set_plant`/`sim_reset_plant`: `Simulation.plants`
    counts a link when its config can `retune` what it built (see its docstring)."""
    import flyball_linux.configs  # noqa: F401  (registers i2c/pwm tags)
    from flyball.runtime.config import load_rig_config
    from flyball_sim.simulation import Simulation

    import humidity.configs  # noqa: F401  (registers sim_humidity_chamber, dual_pump_blender)

    root = Path(__file__).resolve().parents[1]
    config = load_rig_config([root / "rig-multi-sensor.yaml", root / "sim.yaml"])
    rig = config.build(start=False)
    simulation = Simulation(rig, config)
    assert "chamber" in simulation.plants
    assert isinstance(simulation.plants["chamber"], HumidityChamber)
