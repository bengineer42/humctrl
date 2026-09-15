"""The JSON Schema a program file is written against.

Hand-built, because the rules are about which *keys* a flat step carries --
one command, at most one flow, at most one completion -- and pydantic derives
schemas from typed fields instead:

    - setpoint: 60.0
      absolute: 3.2
      minutes: 10.0

Groups compose with `allOf`, so the schema stays additive. The models remain
what runs; a fixture of accepted and rejected programs keeps the two honest.

Two rules live only in the models: an omitted `at` means the setpoint while
the controller runs and the latest reading otherwise, and an inline tuning's
fields depend on its `type`, of which only the presence is checked here.
"""

from __future__ import annotations

from typing import Any

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"

COMMANDS = ("setpoint", "ramp", "regulate", "resume", "tune", "flag", "efforts", "flows")
"""One command per step."""

FLOWS = ("absolute", "full-range-max", "blend-max")
"""How much total flow to deliver. Only meaningful where the pumps are driven."""

COMPLETIONS = ("settle", "above", "below", "confirm")
"""When the step is done. Absent means it completes as soon as it is applied."""

DURATIONS = ("seconds", "minutes", "hours")
"""How long. Absent means the step does not wait."""

RATES = ("per-second", "per-minute", "per-hour")
"""How fast a ramp moves, as an alternative to giving it a duration."""

DRIVES_PUMPS = ("setpoint", "ramp")
"""Commands that drive the pumps, and so may carry a flow."""


def exactly_one(keys: tuple[str, ...]) -> dict[str, Any]:
    """Exactly one of `keys` is present."""
    return {
        "oneOf": [
            {
                "required": [key],
                "not": {"anyOf": [{"required": [other]} for other in keys if other != key]},
            }
            for key in keys
        ]
    }


def at_most_one(keys: tuple[str, ...]) -> dict[str, Any]:
    """At most one of `keys` is present."""
    return {
        "anyOf": [
            exactly_one(keys),
            {"not": {"anyOf": [{"required": [key]} for key in keys]}},
        ]
    }


def at_least_one(keys: tuple[str, ...]) -> dict[str, Any]:
    """At least one of `keys` is present, so a step is never empty."""
    return {"anyOf": [{"required": [key]} for key in keys]}


def only_with(keys: tuple[str, ...], commands: tuple[str, ...]) -> dict[str, Any]:
    """Each of `keys` may appear only alongside one of `commands`."""
    allowed = {"anyOf": [{"required": [command]} for command in commands]}
    return {key: allowed for key in keys}


PERCENT = {"type": "number", "minimum": 0, "maximum": 100}
NORMALISED = {"type": "number", "minimum": 0, "maximum": 1}
POSITIVE = {"type": "number", "exclusiveMinimum": 0}
VALUE = {"anyOf": [PERCENT, {"enum": ["setpoint", "reading"]}]}
"""A value a condition is measured against: a number, or the rig's own."""

TUNING = {
    """A tuning: a name the rig holds, or one inline, of which only `type` is checked."""
    "anyOf": [
        {"type": "string"},
        {"type": "object", "required": ["type"], "properties": {"type": {"type": "string"}}},
    ]
}

AT_DEFAULT = (
    """What an omitted `at` falls back to, for hover text; the rule itself lives in the models."""
    "Defaults to the setpoint while the controller is running, "
    "and to the latest reading when it is not."
)

PUMPS = ("dry", "wet")
"""The two pumps, either of which may be driven alone."""


def settled() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "at": {**VALUE, "description": f"Value to settle around. {AT_DEFAULT}"},
            "within": {**POSITIVE, "description": "Tolerance band, %RH."},
            "readings": {"type": "integer", "minimum": 1},
            **{unit: POSITIVE for unit in DURATIONS},
        },
        "allOf": [at_most_one(DURATIONS)],
    }


def compared() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "at": {**VALUE, "description": f"Threshold. {AT_DEFAULT}"},
            "readings": {"type": "integer", "minimum": 1},
            **{unit: POSITIVE for unit in DURATIONS},
        },
        "allOf": [at_most_one(DURATIONS)],
    }


def confirmed() -> dict[str, Any]:
    """A step that ends when a person says so.

    The short form is the message (`- confirm: "Load the sample"`); only one
    step waits at a time, so there is nothing to name. A limit nests inside,
    since a step-level duration means a dwell; without one an unattended run
    waits forever.
    """
    message = {"type": "string", "description": "Shown to whoever is asked."}
    return {
        "anyOf": [
            message,
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "message": message,
                    **{unit: POSITIVE for unit in DURATIONS},
                },
                "required": ["message"],
                # A duration here is how long to wait before giving up.
                "allOf": [at_most_one(DURATIONS)],
            },
        ]
    }


def step() -> dict[str, Any]:
    """One step: a command, and optionally a flow, a completion and a duration."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "setpoint": {**PERCENT, "description": "Go to this humidity and hold."},
            "ramp": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "from": {**VALUE, "description": "Where the ramp begins."},
                    "to": PERCENT,
                    **{unit: POSITIVE for unit in DURATIONS},
                    **{unit: POSITIVE for unit in RATES},
                },
                "required": ["to"],
                # A ramp is paced either by how long it takes or by how fast it moves.
                "allOf": [exactly_one(DURATIONS + RATES)],
            },
            "regulate": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"at": PERCENT, "using": TUNING},
                "required": ["at"],
            },
            "resume": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"setpoint": PERCENT},
            },
            "tune": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"as": {"type": "string"}},
                "required": ["as"],
            },
            "flag": {"type": "string"},
            "efforts": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"dry": NORMALISED, "wet": NORMALISED},
                # Naming neither pump would drive nothing.
                "allOf": [at_least_one(PUMPS)],
            },
            "flows": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"dry": POSITIVE, "wet": POSITIVE},
                "allOf": [at_least_one(PUMPS)],
            },
            **{key: POSITIVE for key in FLOWS},
            "settle": settled(),
            "above": compared(),
            "below": compared(),
            "confirm": confirmed(),
            **{unit: POSITIVE for unit in DURATIONS},
        },
        "allOf": [
            # A step may be a command, a bare wait, a bare dwell, or a command
            # that then waits -- so each is its own limit, and the step has
            # only to carry one of the three to mean something.
            at_most_one(COMMANDS),
            at_most_one(COMPLETIONS),
            at_least_one(COMMANDS + COMPLETIONS + DURATIONS),
            at_most_one(FLOWS),
            at_most_one(DURATIONS),
        ],
        "dependentSchemas": only_with(FLOWS, DRIVES_PUMPS),
    }


def program_schema() -> dict[str, Any]:
    """The whole document."""
    return {
        "$schema": SCHEMA_URI,
        "title": "humidity program",
        "type": "object",
        "additionalProperties": False,
        "required": ["steps"],
        "properties": {
            "name": {"type": "string"},
            "recording": {"type": "boolean", "default": False},
            "default-tuning": TUNING,
            **{key: POSITIVE for key in FLOWS},
            "steps": {"type": "array", "minItems": 1, "items": step()},
        },
        "allOf": [at_most_one(FLOWS)],
    }


def write(path: str = "program.schema.json") -> None:
    """Emit the schema for an editor to validate a program file against.

    With `# yaml-language-server: $schema=./program.schema.json` at the top of
    a program file, the YAML language server gives completion and inline
    errors. Regenerate in CI: a drifted schema is worse than none.
    """
    import json
    from pathlib import Path

    Path(path).write_text(json.dumps(program_schema(), indent=2) + "\n")


if __name__ == "__main__":
    import sys

    write(*sys.argv[1:])
