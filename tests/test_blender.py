"""The dual-pump blender: one commit per delivery, `together`, the rail, `stop`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from flyball.control import Transfer
from flyball.control.laws import P
from flyball.core.device import Device
from flyball.core.errors import ConflictError
from flyball.core.signal import Access, Node, NodeSpec, Sample, SignalSpec
from flyball.core.typing import Normalised

from humidity.blender import DualPumpBlender, Rail, SupplyHumiditiesError, calculate_wet_fraction
from humidity.pumps import DualPumps, MaxFlows, PumpPair, SupplyHumidities
from humidity.units import HUMIDITY


class RecordingPump:
    """A `PumpDriver` that only remembers what it was told."""

    def __init__(self) -> None:
        self._effort: Normalised = 0.0
        self.calls: list[Normalised] = []

    @property
    def effort(self) -> Normalised:
        return self._effort

    def set_effort(self, effort: Normalised) -> Normalised:
        self.calls.append(effort)
        self._effort = effort
        return effort

    def stop(self) -> None:
        self._effort = 0.0


class Sensors(Device):
    """A dry supply line and the chamber, each an atomic namespace, `[RP]` humidity."""

    TREE = (
        NodeSpec(
            name="dry",
            atomic=True,
            children=(SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP),),
        ),
        NodeSpec(
            name="chamber",
            atomic=True,
            children=(SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP),),
        ),
    )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        raise NotImplementedError  # samples are delivered directly by the tests


@pytest.fixture
def pumps() -> tuple[DualPumps, RecordingPump, RecordingPump]:
    dry, wet = RecordingPump(), RecordingPump()
    return DualPumps(PumpPair(dry, wet), MaxFlows(dry=2.0, wet=2.0)), dry, wet


@pytest.fixture
def blender(
    rig: Any, fresh: Any, pumps: tuple[DualPumps, RecordingPump, RecordingPump]
) -> DualPumpBlender:
    dual_pumps, _, _ = pumps
    device = DualPumpBlender(
        fresh("blender"), dual_pumps, supply=SupplyHumidities(dry=10.0, wet=90.0), blend_flow=1.0
    )
    rig.add_device(device)
    return device


@pytest.fixture
def sensors(rig: Any, fresh: Any) -> Sensors:
    device = Sensors(fresh("hum"))
    rig.add_device(device)
    return device


class TestTree:
    def test_access(self, blender: DualPumpBlender) -> None:
        assert {s.name: s.access for s in blender.signals.values()} == {
            "humidity": Access.W,
            "dry_flow": Access.RPW,
            "wet_flow": Access.RPW,
            "dry_effort": Access.RPW,
            "wet_effort": Access.RPW,
            "blend_flow": Access.RW,
            "expected_humidity": Access.RP,
        }
        assert blender.signals["dry_flow"].limits == (0.0, 2.0)
        assert blender.signals["humidity"].limits == (0.0, 100.0)


class TestTogether:
    def test_a_lone_dry_flow_is_refused(self, rig: Any, blender: DualPumpBlender) -> None:
        with pytest.raises(ConflictError, match=f"'{blender.name}.dry_flow' is set with wet_flow"):
            rig.demand(blender.root, {"dry_flow": 0.4})
        assert blender.pending == {}

    def test_a_lone_dry_effort_is_refused(self, rig: Any, blender: DualPumpBlender) -> None:
        with pytest.raises(
            ConflictError, match=f"'{blender.name}.dry_effort' is set with wet_effort"
        ):
            rig.demand(blender.root, {"dry_effort": 0.5})

    def test_both_together_commits_a_manual_flow(
        self,
        rig: Any,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        rig.demand(blender.root, {"dry_flow": 0.4, "wet_flow": 0.6})
        assert dry.calls == [0.2] and wet.calls == [0.3], "flow / max_flow"


class TestOneCommitPerDelivery:
    def test_a_dry_sample_then_a_chamber_sample_are_two_commits_one_pump_write_each(
        self,
        rig: Any,
        sensors: Sensors,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        dry_h = sensors.signals["dry.humidity"]
        chamber_h = sensors.signals["chamber.humidity"]
        rig.bind_inputs(blender, {"dry": dry_h.address})
        controller = rig.attach_controller(blender.signals["humidity"], chamber_h, law=P(kp=1.0))
        controller.regulate(50.0, transfer=Transfer.RESET)
        assert len(dry.calls) == 1, "arming the controller writes once, outside a delivery"

        rig.on_samples([Sample(sensors.nodes["dry"], 1, {dry_h: 5.0})])
        assert len(dry.calls) == 2, "the bound supply reading: one commit"

        rig.on_samples([Sample(sensors.nodes["chamber"], 2, {chamber_h: 45.0})])
        assert len(dry.calls) == 3, "the controller's source reading: one more commit"
        assert len(wet.calls) == len(dry.calls), "each commit is one write to each line"


class TestRail:
    def test_a_target_outside_the_supply_span_rails_and_is_reported_at_limit(
        self, rig: Any, blender: DualPumpBlender
    ) -> None:
        states = rig.demand(
            blender.root, {"humidity": 200.0}
        )  # clamped to 100 by `limits`, still outside 10-90
        assert states[blender.signals["humidity"]].at_limit == "high"

    def test_calculate_wet_fraction_rails_and_raises_on_a_bad_span(self) -> None:
        humidities = SupplyHumidities(dry=10.0, wet=90.0)
        assert calculate_wet_fraction(humidities, 5.0) is Rail.DRY
        assert calculate_wet_fraction(humidities, 95.0) is Rail.WET
        assert calculate_wet_fraction(humidities, 50.0) == pytest.approx(0.5)
        with pytest.raises(SupplyHumiditiesError):
            calculate_wet_fraction(SupplyHumidities(dry=90.0, wet=10.0), 50.0)


def test_stop_is_a_command_not_a_demand(
    blender: DualPumpBlender, pumps: tuple[DualPumps, RecordingPump, RecordingPump]
) -> None:
    _, dry, wet = pumps
    assert "stop" in blender.commands
    blender.stop()
    assert dry.effort == pytest.approx(0.0) and wet.effort == pytest.approx(0.0)
