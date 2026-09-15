"""The simulated humidity rig's own knobs: noise, supply, chamber physics, and running faster."""

# ruff: file-ignore[float-equality-comparison] -- exact literals on purpose, as the controller's tests do; approx where computed

from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient
from flyball.core.reading import Source
from flyball.runtime.config import RigConfig
from flyball.runtime.simulation import Simulation
from flyball.server import create_app, set_rig, set_simulation
from flyball.server.deps import set_simulation_device
from flyball.sim import ScaledClock

from humidity.readers import Humidity, Temperature
from humidity.sim import HumiditySimulation, build_simulated_rig


@pytest.fixture
def sim():
    rig, simulation = build_simulated_rig(period_s=0.05, tau_s=5.0)
    yield simulation
    rig.readers.stop_all()
    for name in ("process", "dry", "wet"):  # sources register process-wide by name
        Source.forget(name)


def test_the_rig_runs_on_a_scaled_clock(sim):
    assert isinstance(sim.rig.clock, ScaledClock)
    assert sim.state.speed == 1.0 and sim.settings.tau_s == 5.0


def test_set_noise_changes_what_the_chamber_and_supplies_emit(sim):
    sim.set_noise(rh=0.0, c=0.0)
    now = sim.rig.clock.now_ns()
    sample = sim.chamber.sample(now)
    assert sample[Humidity] == sim.chamber.humidity and sample[Temperature] == 21.0
    assert sim.supplies.dry(now)[Humidity] == 10.0 and sim.supplies.wet(now)[Humidity] == 90.0
    sim.set_supply(dry=20.0, wet=95.0)
    assert sim.settings.supply == (20.0, 95.0)
    assert sim.supplies.wet(now)[Humidity] == 95.0
    with pytest.raises(ValueError):
        sim.set_supply(dry=60.0, wet=50.0)
    sim.set_noise(rh=1.0)
    spread = {round(sim.chamber.sample(now)[Humidity], 3) for _ in range(20)}
    assert len(spread) > 1, "noise is back on"


def test_set_chamber_and_reset(sim):
    sim.set_chamber(tau_s=2.0, ambient=70.0)
    assert sim.settings.tau_s == 2.0 and sim.settings.ambient == 70.0
    assert sim.reset().humidity == 70.0
    assert sim.reset(humidity=55.0).humidity == 55.0
    with pytest.raises(ValueError):
        sim.set_chamber(tau_s=0.0)


def _approach(sim: HumiditySimulation, seconds: float) -> float:
    """How far the chamber gets towards a new ambient in `seconds` of real time, from a reset."""
    sim.set_noise(rh=0.0, c=0.0)
    sim.set_chamber(ambient=80.0)
    sim.reset(humidity=40.0)
    time.sleep(seconds)
    return (sim.chamber.humidity - 40.0) / 40.0


def test_running_faster_moves_the_chamber_faster_in_real_seconds(sim):
    clock = sim.rig.clock
    assert isinstance(clock, ScaledClock)
    slow = _approach(sim, 0.3)  # tau 5 s: 1 - e^-0.06, about 6 %
    clock.set_speed(10)
    fast = _approach(sim, 0.3)  # 3 s of rig time: 1 - e^-0.6, about 45 %
    assert sim.state.speed == 10.0
    assert 0.02 < slow < 0.15, slow
    assert fast > 4 * slow, (slow, fast)
    assert fast == pytest.approx(1 - math.exp(-0.6), abs=0.15)


def test_the_device_is_served_at_api_sim_device(sim):
    rig = sim.rig
    set_rig(rig)
    set_simulation(Simulation(rig, RigConfig(name="humidity-sim")))
    set_simulation_device(sim)
    try:
        with TestClient(create_app()) as c:
            schema = c.get("/api/sim/device/schema").json()
            assert schema["type"] == "HumiditySimulation"
            assert set(schema["commands"]) == {"set_chamber", "set_noise", "set_supply", "reset"}
            assert set(schema["settings"]["properties"]) == {
                "tau_s",
                "ambient",
                "noise_rh",
                "noise_c",
                "supply",
            }
            assert c.post("/api/sim/device/set_noise", json={"rh": 0}).json()["noise_rh"] == 0.0
            view = c.get("/api/sim/device").json()
            assert view["settings"]["noise_rh"] == 0.0 and view["state"]["speed"] == 1.0
            assert c.put("/api/sim/clock", json={"speed": 10}).json() == {"speed": 10.0}
            assert c.get("/api/sim/device").json()["state"]["speed"] == 10.0
            assert c.get("/api/sim").json()["clock"]["speed"] == 10.0
    finally:
        set_simulation_device(None)
        set_simulation(None)
        set_rig(None)
