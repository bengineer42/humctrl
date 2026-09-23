"""The dual-pump blender: one commit per delivery, `together`, the rail, `stop`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from flyball.control.laws import P
from flyball.foundation.device import (
    Access,
    Limit,
    Node,
    NodeSpec,
    Readable,
    Role,
    Sample,
    SignalSpec,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.typing import Normalised
from flyball.model.law import Transfer
from flyball_linux.links.pwm import FakePwm
from pydantic import TypeAdapter

from humidity.blender import (
    DualPumpBlender,
    DualPumpBlenderConfig,
    Mode,
    PumpLineConfig,
    SupplyConfig,
    SupplyHumiditiesError,
    calculate_wet_fraction,
)
from humidity.pumps import DualPumps, MaxFlows, PumpPair, PwmPump, SupplyHumidities
from humidity.units import HUMIDITY, WET_FRACTION, Flow


def test_flow_alias_carries_unit_and_quantity_in_the_schema() -> None:
    schema = TypeAdapter(Flow).json_schema()
    assert schema["unit"] == "L/min"
    assert schema["quantity"] == "flow"


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


class Sensors(Readable):
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
    def test_roles_access_and_sections(self, blender: DualPumpBlender) -> None:
        roles = {path: (s.role, s.access) for path, s in blender.signals.items()}
        assert roles["humidity"] == (Role.DEMAND, Access.RPW)
        assert roles["flows.dry"] == roles["flows.wet"] == (Role.DEMAND, Access.RP), (
            "readback only: set_flows is the only way to move them"
        )
        assert roles["efforts.dry"] == roles["efforts.wet"] == (Role.DEMAND, Access.RP), (
            "readback only: set_efforts is the only way to move them"
        )
        assert roles["expected_humidity"] == (Role.READOUT, Access.RP)
        assert roles["mode"] == (Role.READOUT, Access.RP)
        assert roles["blend.flow"] == (Role.SETTING, Access.RP)
        assert roles["blend.wet_fraction"] == (Role.DEMAND, Access.RP), (
            "readback only: set_fraction is the only way to move it"
        )
        assert roles["max_flows.dry"] == (Role.CONFIG, Access.R)
        assert blender.signals["flows.dry"].tags == {"line": "dry"}
        assert blender.signals["efforts.wet"].tags == {"line": "wet"}
        assert blender.dry_flow.limits == (0.0, 2.0), "from the max_flows.dry config signal"
        assert blender.humidity.limits == (10.0, 90.0), "the supply inputs' defaults, unbound"
        assert blender.mode.value is Mode.MANUAL, "nothing drives the pumps until asked"
        assert blender.dry_max_flow.value == pytest.approx(2.0)

    def test_commands_and_their_links(self) -> None:
        commands = DualPumpBlender.commands
        expected = {"set_blend", "set_flows", "set_efforts", "set_fraction", "stop", "set_humidity"}
        assert expected <= set(commands)
        assert commands["set_fraction"].params["wet_fraction"].link == "blend.wet_fraction"
        assert {n: p.link for n, p in commands["set_flows"].params.items()} == {
            "dry": "flows.dry",
            "wet": "flows.wet",
        }
        assert commands["set_flows"].mode is Mode.MANUAL
        assert commands["stop"].interrupts and commands["set_flows"].interrupts
        assert commands["set_humidity"].demand_of is None, "a real command, not a synthesised one"
        assert commands["set_humidity"].params["humidity"].link == "humidity"
        assert commands["set_humidity"].mode is Mode.BLEND and commands["set_humidity"].interrupts
        assert "set_flows_dry" not in commands, "a demand a command sets gets no setter"


class TestCommands:
    def test_set_flows_drives_the_lines_and_changes_the_mode(
        self,
        rig: Any,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        rig.run_command(blender, "set_flows", {"dry": 0.4, "wet": 0.6})
        assert dry.calls == [0.2] and wet.calls == [0.3], "flow / max_flow"
        assert blender.mode.value is Mode.MANUAL
        assert blender.dry_flow.value == pytest.approx(0.4)
        assert blender.dry_effort.value == pytest.approx(0.2), "the readbacks follow the pumps"
        assert rig.router.reading(blender.signals["last.set_flows"]) is not None

    def test_a_line_left_out_keeps_its_current_flow(
        self,
        rig: Any,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        rig.run_command(blender, "set_flows", {"dry": 0.4, "wet": 0.6})
        rig.run_command(blender, "set_flows", {"wet": 1.0})
        assert dry.calls[-1] == pytest.approx(0.2) and wet.calls[-1] == pytest.approx(0.5)

    def test_a_flow_past_the_line_s_max_is_clamped(
        self,
        rig: Any,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, _ = pumps
        rig.run_command(blender, "set_flows", {"dry": 5.0, "wet": 0.0})
        assert dry.calls == [1.0]

    def test_a_manual_flow_survives_a_supply_reading(
        self,
        rig: Any,
        sensors: Sensors,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, _ = pumps
        dry_h = sensors.signals["dry.humidity"]
        rig.bind_inputs(blender, {"dry": dry_h.address})
        rig.run_command(blender, "set_flows", {"dry": 0.4, "wet": 0.6})
        rig.on_samples([Sample(sensors.nodes["dry"], 1, {dry_h: 5.0})])
        assert dry.calls == [0.2], "not in BLEND: the supply reading does not re-blend"
        rig.write(blender.root, {"humidity": 50.0})
        assert blender.mode.value is Mode.BLEND and len(dry.calls) == 2

    def test_set_humidity_blends_by_hand_and_takes_the_controller_to_manual(
        self,
        rig: Any,
        sensors: Sensors,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        chamber_h = sensors.signals["chamber.humidity"]
        controller = rig.attach_controller(blender.humidity, chamber_h, law=P(kp=1.0))
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.run_command(blender, "set_humidity", {"humidity": 90.0})  # blend flow left as it is
        assert not controller.mode.active()
        assert blender.mode.value is Mode.BLEND
        assert blender.humidity.value == pytest.approx(90.0)
        assert wet.calls[-1] == pytest.approx(0.5) and dry.calls[-1] == pytest.approx(0.0), (
            "1 L/min, all wet, on a 2 L/min line"
        )
        assert blender.wet_fraction.value == pytest.approx(1.0)

    def test_a_manual_command_interrupts_the_controller(
        self, rig: Any, sensors: Sensors, blender: DualPumpBlender
    ) -> None:
        chamber_h = sensors.signals["chamber.humidity"]
        controller = rig.attach_controller(blender.humidity, chamber_h, law=P(kp=1.0))
        controller.regulate(50.0, transfer=Transfer.COLD)
        rig.run_command(blender, "set_flows", {"dry": 0.4, "wet": 0.6})
        assert not controller.mode.active(), "put in manual, with an event"
        assert rig.recent[-1].kind == "interrupted"
        assert blender.mode.value is Mode.MANUAL
        controller.regulate(50.0, transfer=Transfer.COLD)
        assert blender.mode.value is Mode.BLEND, "a humidity demand takes it back"


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
        controller = rig.attach_controller(blender.humidity, chamber_h, law=P(kp=1.0))
        controller.regulate(50.0, transfer=Transfer.COLD)
        assert len(dry.calls) == 1, "arming the controller writes once, outside a delivery"

        rig.on_samples([Sample(sensors.nodes["dry"], 1, {dry_h: 5.0})])
        assert len(dry.calls) == 2, "the bound supply reading: one commit"

        rig.on_samples([Sample(sensors.nodes["chamber"], 2, {chamber_h: 45.0})])
        assert len(dry.calls) == 3, "the controller's source reading: one more commit"
        assert len(wet.calls) == len(dry.calls), "each commit is one write to each line"


class TestSupplyLimits:
    def test_the_target_is_clamped_to_what_the_bound_supply_lines_read(
        self, rig: Any, sensors: Sensors, blender: DualPumpBlender
    ) -> None:
        dry_h = sensors.signals["dry.humidity"]
        rig.bind_inputs(blender, {"dry": dry_h.address})
        rig.on_samples([Sample(sensors.nodes["dry"], 1, {dry_h: 25.0})])
        assert blender.humidity.limits == (25.0, 90.0), "the bound dry line, the wet default"
        states = rig.write(blender.root, {"humidity": 5.0})
        assert states[blender.humidity].value == pytest.approx(25.0)
        assert states[blender.humidity].requested == pytest.approx(5.0)
        assert states[blender.humidity].at_limit == Limit.LOW


class TestFlowsAndEffortsNotDirectlyWritable:
    """`efforts.*`/`flows.*`/`blend.wet_fraction` are readbacks.

    A dashboard write must be refused, not silently accepted and dropped --
    see `DualPumpBlender.commit`, which never reads their `.staged`.
    """

    @pytest.mark.parametrize(
        "address", ["efforts.dry", "efforts.wet", "flows.dry", "flows.wet", "blend.wet_fraction"]
    )
    def test_a_direct_write_is_refused(
        self, rig: Any, blender: DualPumpBlender, address: str
    ) -> None:
        with pytest.raises(ConflictError, match="not writable"):
            rig.write(blender.root, {address: 0.5})

    def test_set_efforts_still_moves_the_pumps(
        self,
        rig: Any,
        blender: DualPumpBlender,
        pumps: tuple[DualPumps, RecordingPump, RecordingPump],
    ) -> None:
        _, dry, wet = pumps
        rig.run_command(blender, "set_efforts", {"dry": 0.3, "wet": 0.7})
        assert dry.calls[-1] == pytest.approx(0.3)
        assert wet.calls[-1] == pytest.approx(0.7)
        assert blender.dry_effort.value == pytest.approx(0.3), (
            "the command still updates the readback"
        )

    def test_set_fraction_still_moves_the_pumps_and_links_its_argument(
        self, rig: Any, blender: DualPumpBlender
    ) -> None:
        rig.run_command(blender, "set_fraction", {"wet_fraction": 0.75})
        assert blender.wet_fraction.value == pytest.approx(0.75), (
            "the command still updates the readback"
        )
        param = DualPumpBlender.commands["set_fraction"].params["wet_fraction"]
        assert param.link == "blend.wet_fraction"
        assert blender.wet_fraction.quantity == WET_FRACTION
        assert blender.wet_fraction.limits == (0.0, 1.0)


class TestRail:
    def test_a_target_outside_the_supply_span_rails_and_is_reported_at_limit(
        self, rig: Any, blender: DualPumpBlender
    ) -> None:
        states = rig.write(
            blender.root, {"humidity": 200.0}
        )  # clamped to 100 by `limits`, still outside 10-90
        assert states[blender.humidity].at_limit == "high"

    def test_calculate_wet_fraction_rails_and_raises_on_a_bad_span(self) -> None:
        humidities = SupplyHumidities(dry=10.0, wet=90.0)
        assert calculate_wet_fraction(humidities, 5.0) is Limit.LOW
        assert calculate_wet_fraction(humidities, 95.0) is Limit.HIGH
        assert calculate_wet_fraction(humidities, 50.0) == pytest.approx(0.5)
        with pytest.raises(SupplyHumiditiesError):
            calculate_wet_fraction(SupplyHumidities(dry=90.0, wet=10.0), 50.0)


def test_stop_is_a_command_not_a_demand(
    rig: Any, blender: DualPumpBlender, pumps: tuple[DualPumps, RecordingPump, RecordingPump]
) -> None:
    _, dry, wet = pumps
    assert "stop" in blender.commands
    rig.run_command(blender, "stop")
    assert dry.effort == pytest.approx(0.0) and wet.effort == pytest.approx(0.0)
    assert blender.dry_flow.value == pytest.approx(0.0) and blender.mode.value is Mode.MANUAL


class TestPwmPump:
    """The pumps write duties through `PwmLink`, not their own hardware library."""

    def test_set_effort_configures_the_channel_with_a_deadband(self) -> None:
        chip = FakePwm()
        pump = PwmPump(chip, channel=1, frequency_hz=1000.0, deadband=0.1)
        pump.set_effort(0.5)
        period_ns, duty_ns = chip.channels[1]
        assert period_ns == 1_000_000  # 1 kHz
        assert duty_ns == round((0.9 * 0.5 + 0.1) * period_ns)
        assert chip.enabled[1] is True

        pump.set_effort(0.0)
        assert chip.enabled[1] is False

    def test_stop_disables_the_channel(self) -> None:
        chip = FakePwm()
        pump = PwmPump(chip, channel=0, frequency_hz=20_000.0)
        pump.set_effort(1.0)
        pump.stop()
        assert pump.effort == pytest.approx(0.0)
        assert chip.enabled[0] is False


def test_the_config_builds_a_working_blender_on_a_fake_pwm_chip(fresh: Any) -> None:
    chip = FakePwm()
    config = DualPumpBlenderConfig(
        link="pwm0",
        dry=PumpLineConfig(channel=0, deadband=0.05, max_flow=2.0),
        wet=PumpLineConfig(channel=1, deadband=0.05, max_flow=2.0),
        supply=SupplyConfig(dry=10.0, wet=90.0),
    )
    device = config.model_copy(update={"link": chip}).build(fresh("blender"))
    device.set_flows(0.4, 0.6)
    assert chip.channels.keys() == {0, 1}
    assert chip.enabled == {0: True, 1: True}
