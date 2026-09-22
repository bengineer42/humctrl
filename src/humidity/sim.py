"""A humidity chamber, physically parameterised, doubling as a fake PWM chip.

`HumidityChamber` mixes a dry and a wet air stream (`dry_rh`, `wet_rh`,
their own maximum flows) into a chamber of `volume_l`, leaking towards
`ambient_rh` at `exchange_per_min`; the chamber's sensor lags the true
value by `sensor_tau_s`, and the mixing equation itself lags the commanded
flows by `dead_time_s` -- transport delay, downstream of nothing (the sensor
lag is separate and comes after it). It is two things at once:

* a `MultiPlant` (`flyball_sim.plant`) -- `sim_daq` (`hum_sensors`) reads
  its six named outputs, one per leaf the real `hum_sensors` declares
  (`chamber_humidity`, `chamber_temperature`, `dry_humidity`,
  `dry_temperature`, `wet_humidity`, `wet_temperature`);
* a `flyball_linux.links.pwm.PwmLink` (`configure`/`enable`) -- the real
  `dual_pump_blender` driver (unmodified) drives it exactly as it would a
  hardware PWM chip, channel 0 the dry line and channel 1 the wet line,
  matching `rig-multi-sensor.yaml`'s `dry.channel`/`wet.channel`. So `sim.yaml`'s
  `blender` device is the genuine `DualPumpBlender` -- every signal, unit,
  role and command matches the real rig exactly, because it is the same
  class; `expected_humidity`, `flows.*`, `efforts.*` and `mode` are the
  blender's own bookkeeping, not read back from the chamber, precisely as
  on the real rig (plan §1.6).

The dry and wet supplies drift slowly (a sinusoid, decorrelated by phase)
and carry their own reading noise, so the "Inputs" page is not a flat
line; every temperature drifts the same slow way, the chamber's also
warming a little with total flow. None of this is physically coupled
back into anything but the chamber's own humidity, which *is* driven by
the (drifting) supply humidities actually delivered.
"""

from __future__ import annotations

import random
from collections import deque
from math import pi, sin
from typing import Any, Literal

from flyball.foundation.config import Config
from flyball.foundation.typing import NonNegative, Positive

from humidity.blender import SupplyHumiditiesError
from humidity.pumps import SupplyHumidities
from humidity.units import Humidity, Temperature

Port = Literal[
    "chamber_humidity",
    "chamber_temperature",
    "dry_humidity",
    "dry_temperature",
    "wet_humidity",
    "wet_temperature",
]
PORTS: tuple[Port, ...] = (
    "chamber_humidity",
    "chamber_temperature",
    "dry_humidity",
    "dry_temperature",
    "wet_humidity",
    "wet_temperature",
)

DRY_CHANNEL = 0
"""The PWM channel `configure`/`enable` treats as the dry line -- `rig-multi-sensor.yaml`'s `dry.channel`."""
WET_CHANNEL = 1
"""The PWM channel `configure`/`enable` treats as the wet line -- `rig-multi-sensor.yaml`'s `wet.channel`."""

# Each drifting signal gets its own phase on the one slow sinusoid, so the
# six traces wander independently instead of moving in lockstep.
_DRY_HUMIDITY_PHASE = 0.0
_WET_HUMIDITY_PHASE = 0.5 * pi
_CHAMBER_TEMPERATURE_PHASE = 0.25 * pi
_DRY_TEMPERATURE_PHASE = pi
_WET_TEMPERATURE_PHASE = 1.5 * pi

_MIN_STEP_S = 0.05


class HumidityChamber:
    """A `MultiPlant` and a `PwmLink` at once: see the module docstring."""

    def __init__(
        self,
        *,
        volume_l: float,
        dry_flow_l_per_min: float,
        wet_flow_l_per_min: float,
        flow_l_per_min: float,
        dry_rh: float,
        wet_rh: float,
        ambient_rh: float,
        exchange_per_min: float,
        initial_rh: float,
        temperature_c: float,
        sensor_tau_s: float,
        dead_time_s: float,
        noise_rh: float,
        supply_noise_rh: float,
        supply_drift_rh: float,
        supply_drift_period_s: float,
        temperature_noise_c: float,
        temperature_drift_c: float,
        flow_warming_c_per_lpm: float,
        seed: int | None,
    ) -> None:
        self.inputs: dict[str, float] = {}
        """Empty: this plant is driven through `configure`/`enable`, not a named input."""
        self.output_names = PORTS
        self._volume_l = volume_l
        self._dry_flow_l_per_min = dry_flow_l_per_min
        self._wet_flow_l_per_min = wet_flow_l_per_min
        self._flow_l_per_min = flow_l_per_min
        self._dry_rh = dry_rh
        self._wet_rh = wet_rh
        self._ambient_rh = ambient_rh
        self._exchange_per_min = exchange_per_min
        self._temperature_c = temperature_c
        self._sensor_tau_s = sensor_tau_s
        self._dead_time_s = dead_time_s
        self._noise_rh = noise_rh
        self._supply_noise_rh = supply_noise_rh
        self._supply_drift_rh = supply_drift_rh
        self._supply_drift_period_s = supply_drift_period_s
        self._temperature_noise_c = temperature_noise_c
        self._temperature_drift_c = temperature_drift_c
        self._flow_warming_c_per_lpm = flow_warming_c_per_lpm
        self._max_step_s = max(_MIN_STEP_S, min(1.0, sensor_tau_s / 4.0))
        self._h = initial_rh
        self._sensor = initial_rh
        self._last_ns: int | None = None
        self._flow_history: deque[tuple[int, float, float]] = deque()
        """Commanded `(time_ns, dry_flow, wet_flow)`, oldest first; feeds `_delayed_flows`."""
        self._duty: dict[int, float] = {}
        self._enabled: dict[int, bool] = {}
        self._random = random.Random(seed)

    # region PwmLink -- the real `dual_pump_blender` drives these

    def configure(self, channel: int, period_ns: int, duty_ns: int) -> None:
        self._duty[channel] = duty_ns / period_ns if period_ns else 0.0

    def enable(self, channel: int, on: bool) -> None:
        self._enabled[channel] = on

    def _effort(self, channel: int) -> float:
        """The channel's commanded duty, 0 if never configured or currently disabled."""
        return self._duty.get(channel, 0.0) if self._enabled.get(channel, False) else 0.0

    @property
    def _flows(self) -> tuple[float, float]:
        """Litres/min actually leaving each line, from its own channel's duty."""
        return (
            self._effort(DRY_CHANNEL) * self._dry_flow_l_per_min,
            self._effort(WET_CHANNEL) * self._wet_flow_l_per_min,
        )

    # endregion
    # region MultiPlant -- `sim_daq` (`hum_sensors`) reads these

    def output(self, port: str) -> float:
        time_ns = self._last_ns or 0
        if port == "chamber_humidity":
            return _clamp_rh(self._random.gauss(self._sensor, self._noise_rh))
        if port == "dry_humidity":
            return self._supply_rh(time_ns)[0] + self._random.gauss(0.0, self._supply_noise_rh)
        if port == "wet_humidity":
            return self._supply_rh(time_ns)[1] + self._random.gauss(0.0, self._supply_noise_rh)
        if port in ("chamber_temperature", "dry_temperature", "wet_temperature"):
            return self._temperature(port, time_ns)
        raise ValueError(f"no output {port!r}; there are {self.output_names}")

    def advance(self, time_ns: int) -> None:
        """Step the chamber to `time_ns`, in sub-steps short enough for the sensor lag."""
        if self._last_ns is not None and time_ns > self._last_ns:
            remaining = (time_ns - self._last_ns) / 1e9
            t_ns = self._last_ns
            while remaining > 1e-9:
                step = min(remaining, self._max_step_s)
                self._euler(step, t_ns)
                t_ns += round(step * 1e9)
                remaining -= step
        self._last_ns = time_ns

    def feedforward(self, port: str, demand: float) -> float:
        """The `wet_fraction` that would settle `demand`, at `flow_l_per_min`, unclamped."""
        self._check_input(port)
        h_in = self._steady_h_in(demand)
        span = self._wet_rh - self._dry_rh
        return (h_in - self._dry_rh) / span

    def inverse_feedforward(self, port: str, drive: float) -> float:
        """The humidity a steady `wet_fraction` of `drive` settles at, at `flow_l_per_min`."""
        self._check_input(port)
        h_in = self._dry_rh + drive * (self._wet_rh - self._dry_rh)
        return self._steady_h(h_in)

    def _check_input(self, port: str) -> None:
        if port != "wet_fraction":
            raise ValueError(f"no input {port!r}; there are ('wet_fraction',)")

    def _steady_h(self, h_in: float) -> float:
        q, v, e = self._flow_l_per_min, self._volume_l, self._exchange_per_min
        denom = q + e * v
        if denom <= 0:
            raise ValueError("no flow and no exchange: the chamber has no static map")
        return (q * h_in + e * v * self._ambient_rh) / denom

    def _steady_h_in(self, demand: float) -> float:
        q, v, e = self._flow_l_per_min, self._volume_l, self._exchange_per_min
        if q <= 0:
            raise ValueError("flow_l_per_min is 0: wet_fraction has no effect at steady state")
        return (demand * (q + e * v) - e * v * self._ambient_rh) / q

    # endregion
    # region The physics

    def _euler(self, dt_s: float, t_ns: int) -> None:
        self._flow_history.append((t_ns, *self._flows))
        dry_flow, wet_flow = self._delayed_flows(t_ns)
        total = dry_flow + wet_flow
        dry_rh, wet_rh = self._supply_rh(t_ns)
        h_in = (dry_flow * dry_rh + wet_flow * wet_rh) / total if total > 0 else self._h
        rate_per_min = (total / self._volume_l) * (h_in - self._h) + self._exchange_per_min * (
            self._ambient_rh - self._h
        )
        self._h += rate_per_min * dt_s / 60.0
        if self._sensor_tau_s > 0:
            self._sensor += (self._h - self._sensor) * dt_s / self._sensor_tau_s
        else:
            self._sensor = self._h

    def _delayed_flows(self, time_ns: int) -> tuple[float, float]:
        """The commanded dry/wet flows as they stood `dead_time_s` ago.

        Transport delay: `configure`/`enable` change what is *in the pipe*, not what is
        reaching the chamber right now -- unlike `sensor_tau_s`, which lags the *reading*.
        Looks up the newest history entry at or before `time_ns - dead_time_s`, draining
        anything older (time only moves forward, so an older entry can never be wanted
        again). Startup, before any command is that old yet: returns `(0.0, 0.0)`, the same
        as if the line had never been commanded -- the dead-time pipe has nothing in it.
        """
        if self._dead_time_s <= 0:
            return self._flows
        target_ns = time_ns - round(self._dead_time_s * 1e9)
        history = self._flow_history
        if not history or history[0][0] > target_ns:
            return (0.0, 0.0)
        while len(history) > 1 and history[1][0] <= target_ns:
            history.popleft()
        return history[0][1], history[0][2]

    def _drift(self, time_ns: int, amplitude: float, phase: float) -> float:
        if amplitude <= 0:
            return 0.0
        return amplitude * sin(2.0 * pi * (time_ns / 1e9) / self._supply_drift_period_s + phase)

    def _supply_rh(self, time_ns: int) -> tuple[float, float]:
        dry = self._dry_rh + self._drift(time_ns, self._supply_drift_rh, _DRY_HUMIDITY_PHASE)
        wet = self._wet_rh + self._drift(time_ns, self._supply_drift_rh, _WET_HUMIDITY_PHASE)
        return dry, wet

    def _temperature(self, port: str, time_ns: int) -> float:
        phase = {
            "chamber_temperature": _CHAMBER_TEMPERATURE_PHASE,
            "dry_temperature": _DRY_TEMPERATURE_PHASE,
            "wet_temperature": _WET_TEMPERATURE_PHASE,
        }[port]
        base = self._temperature_c + self._drift(time_ns, self._temperature_drift_c, phase)
        if port == "chamber_temperature":
            base += self._flow_warming_c_per_lpm * sum(self._flows)
        return base + self._random.gauss(0.0, self._temperature_noise_c)

    # endregion


def _clamp_rh(value: float) -> float:
    return min(100.0, max(0.0, value))


class HumidityChamberConfig(Config[HumidityChamber], tag="sim_humidity_chamber"):
    """A mixing-model chamber; every field is physical, and documents its own unit.

    Steady state (no drift, no clamp): `wet_fraction` 0 rests at `dry_rh`, 1
    at `wet_rh`, in between along that span, diluted further towards
    `ambient_rh` by `exchange_per_min`. Live drive comes from the real
    `dual_pump_blender`, through `configure`/`enable` (see the module
    docstring) -- `dry_flow_l_per_min`/`wet_flow_l_per_min` should equal
    that device's own `dry.max_flow`/`wet.max_flow`, so what the blender
    believes it delivers is what the chamber actually receives.
    """

    volume_l: Positive = 20.0
    """The chamber's volume, litres."""
    dry_flow_l_per_min: Positive = 2.0
    """The dry line's flow at full duty -- match the blender's `dry.max_flow`."""
    wet_flow_l_per_min: Positive = 2.0
    """The wet line's flow at full duty -- match the blender's `wet.max_flow`."""
    flow_l_per_min: Positive = 1.0
    """The nominal total flow `feedforward`/`inverse_feedforward` assume; not used live."""
    dry_rh: Humidity = 10.0
    """The dry supply's humidity at rest, before drift, %RH."""
    wet_rh: Humidity = 90.0
    """The wet supply's humidity at rest, before drift, %RH."""
    ambient_rh: Humidity = 45.0
    """The room's humidity the chamber leaks towards, %RH."""
    exchange_per_min: NonNegative = 0.01
    """The chamber's air exchange with the room (leak), a fraction of its volume per minute."""
    initial_rh: Humidity = 40.0
    """The chamber's starting humidity, %RH."""
    temperature_c: Temperature = 21.0
    """Every temperature output's steady baseline, °C."""
    sensor_tau_s: Positive = 3.0
    """The chamber sensor's own first-order lag, seconds."""
    dead_time_s: NonNegative = 0.0
    """Transport delay: how long a `configure`/`enable` change takes to reach the mixing
    equation, seconds. Not `sensor_tau_s` -- that lags the *reading*, this delays the
    *effect*. Default 0.0 (no delay), matching every existing rig file."""
    noise_rh: NonNegative = 0.3
    """Gaussian noise on the chamber's humidity reading, %RH."""
    supply_noise_rh: NonNegative = 0.15
    """Gaussian noise on the dry/wet humidity readings, each sample, %RH."""
    supply_drift_rh: NonNegative = 2.0
    """Amplitude of a slow sinusoidal drift on the supplies' humidity, ± %RH."""
    supply_drift_period_s: Positive = 600.0
    """The period of the supply drift, and of the slower temperature drift, seconds."""
    temperature_noise_c: NonNegative = 0.05
    """Gaussian noise on every temperature reading, °C."""
    temperature_drift_c: NonNegative = 0.3
    """Amplitude of a slow sinusoidal drift on every temperature around `temperature_c`, ± °C."""
    flow_warming_c_per_lpm: NonNegative = 0.1
    """How much the chamber warms per L/min of total flow through it, °C per L/min."""
    seed: int | None = None

    def build(self) -> HumidityChamber:
        humidities = SupplyHumidities(self.dry_rh, self.wet_rh)
        if humidities.wet <= humidities.dry:
            raise SupplyHumiditiesError(humidities)
        return HumidityChamber(
            volume_l=self.volume_l,
            dry_flow_l_per_min=self.dry_flow_l_per_min,
            wet_flow_l_per_min=self.wet_flow_l_per_min,
            flow_l_per_min=self.flow_l_per_min,
            dry_rh=self.dry_rh,
            wet_rh=self.wet_rh,
            ambient_rh=self.ambient_rh,
            exchange_per_min=self.exchange_per_min,
            initial_rh=self.initial_rh,
            temperature_c=self.temperature_c,
            sensor_tau_s=self.sensor_tau_s,
            dead_time_s=self.dead_time_s,
            noise_rh=self.noise_rh,
            supply_noise_rh=self.supply_noise_rh,
            supply_drift_rh=self.supply_drift_rh,
            supply_drift_period_s=self.supply_drift_period_s,
            temperature_noise_c=self.temperature_noise_c,
            temperature_drift_c=self.temperature_drift_c,
            flow_warming_c_per_lpm=self.flow_warming_c_per_lpm,
            seed=self.seed,
        )

    def retune(self, plant: Any) -> None:
        """Apply this config's parameters to a running chamber (`sim_set_plant`).

        Its state -- current humidity (`_h`), sensor reading (`_sensor`), the
        commanded-flow history the dead time reads from, and the PWM duty/enable a
        driver already set -- is left untouched, exactly as the furnace's `retune`
        leaves its temperatures alone: a simulation keeps running through the change,
        as a real rig would. `initial_rh` and `seed` are start-up-only and are not
        reapplied, again following the furnace's precedent for `initial_c`.

        Raises:
            ValueError: `wet_rh` would no longer be greater than `dry_rh`. Unlike a
                plain parameter, the blend direction is structural here (the mixing
                equation and `feedforward`/`inverse_feedforward` assume dry-to-wet is a
                span with a fixed sign) -- the equivalent of the furnace refusing a
                change of zone count, or `PlantConfig.retune` refusing a change of model.
        """
        if not isinstance(plant, HumidityChamber):
            raise ValueError(f"not a HumidityChamber: {plant!r}")
        if self.wet_rh <= self.dry_rh:
            raise ValueError(
                f"wet_rh ({self.wet_rh}) must stay greater than dry_rh ({self.dry_rh}): "
                "the blend direction cannot change while the chamber runs"
            )
        fresh = self.build()
        for attr in (
            "_volume_l",
            "_dry_flow_l_per_min",
            "_wet_flow_l_per_min",
            "_flow_l_per_min",
            "_dry_rh",
            "_wet_rh",
            "_ambient_rh",
            "_exchange_per_min",
            "_temperature_c",
            "_sensor_tau_s",
            "_dead_time_s",
            "_noise_rh",
            "_supply_noise_rh",
            "_supply_drift_rh",
            "_supply_drift_period_s",
            "_temperature_noise_c",
            "_temperature_drift_c",
            "_flow_warming_c_per_lpm",
            "_max_step_s",
        ):
            setattr(plant, attr, getattr(fresh, attr))
