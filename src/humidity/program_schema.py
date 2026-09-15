"""The JSON Schema a program file is written against.

Hand-built rather than generated from the models, because the rules are about
which *keys* are present across a flat step -- one command, at most one flow, at
most one completion -- and pydantic derives schemas from typed fields instead.

A step therefore stays flat::

    - setpoint: 60.0
      absolute: 3.2
      minutes: 10.0

Composing the groups with ``allOf`` keeps the schema additive: four groups of
three cost twelve subschemas, not eighty-one combinations.

The models remain the thing that runs. Keep the two honest with a fixture of
programs that must be accepted, and malformed ones that must be rejected, by
both paths.

Two rules deliberately live in the models rather than here, because no schema
can carry them:

- An omitted ``at`` means the setpoint while the controller is running, and
  the latest reading when it is not. Which one it is depends on the state of
  the rig at the moment the step is reached, not on the file.
- An inline tuning's fields are whatever its ``type`` names, and the set of
  laws is open. Only the presence of ``type`` is checked here.
"""

from __future__ import annotations

from typing import Any

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"

#: One command per step.
COMMANDS = ("setpoint", "ramp", "regulate", "resume", "tune", "flag", "efforts", "flows")

#: How much total flow to deliver. Only meaningful where the pumps are driven.
FLOWS = ("absolute", "full-range-max", "blend-max")

#: When the step is done. Absent means it completes as soon as it is applied.
COMPLETIONS = ("settle", "above", "below", "confirm")

#: How long. Absent means the step does not wait.
DURATIONS = ("seconds", "minutes", "hours")

#: How fast a ramp moves, as an alternative to giving it a duration.
RATES = ("per-second", "per-minute", "per-hour")

#: Commands that drive the pumps, and so may carry a flow.
DRIVES_PUMPS = ("setpoint", "ramp")


def exactly_one(keys: tuple[str, ...]) -> dict[str, Any]:
    """Exactly one of ``keys`` is present."""
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
    """At most one of ``keys`` is present."""
    return {
        "anyOf": [
            exactly_one(keys),
            {"not": {"anyOf": [{"required": [key]} for key in keys]}},
        ]
    }


def at_least_one(keys: tuple[str, ...]) -> dict[str, Any]:
    """At least one of ``keys`` is present, so a step is never empty."""
    return {"anyOf": [{"required": [key]} for key in keys]}


def only_with(keys: tuple[str, ...], commands: tuple[str, ...]) -> dict[str, Any]:
    """Each of ``keys`` may appear only alongside one of ``commands``."""
    allowed = {"anyOf": [{"required": [command]} for command in commands]}
    return {key: allowed for key in keys}


PERCENT = {"type": "number", "minimum": 0, "maximum": 100}
NORMALISED = {"type": "number", "minimum": 0, "maximum": 1}
POSITIVE = {"type": "number", "exclusiveMinimum": 0}
#: A value a condition is measured against: a number, or the rig's own.
VALUE = {"anyOf": [PERCENT, {"enum": ["setpoint", "reading"]}]}

#: A tuning: the name of one the rig already holds, or one written out in
#: place. The inline form's fields belong to whichever control law ``type``
#: picks, so the schema checks only that the law is named and leaves the rest
#: to the law's own model -- it is the one place here that cannot be closed.
TUNING = {
    "anyOf": [
        {"type": "string"},
        {"type": "object", "required": ["type"], "properties": {"type": {"type": "string"}}},
    ]
}

#: What an omitted ``at`` falls back to. Repeated into the hover text so it
#: is visible while writing a step; the rule itself lives in the models,
#: because it turns on the state of the rig rather than on the file.
AT_DEFAULT = (
    "Defaults to the setpoint while the controller is running, "
    "and to the latest reading when it is not."
)

#: The two pumps, either of which may be driven alone.
PUMPS = ("dry", "wet")


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

    The short form is the message itself, because only one step of a program
    is ever waiting at a time and so there is nothing to name::

        - confirm: "Load the sample and close the lid"

    A limit nests inside rather than sitting at step level, where a duration
    means a dwell. Without one an unattended run parks at setpoint forever,
    waiting on a lid nobody is going to close.
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
    """Emit the schema, for an editor to validate a program file against.

    Put a directive at the top of the program file and the YAML language server
    gives autocomplete, hover text and inline errors while you write::

        # yaml-language-server: $schema=./program.schema.json

    Regenerate this in CI: a schema that has drifted from the models is worse
    than none, because the editor confidently reports the wrong thing.
    """
    import json
    from pathlib import Path

    Path(path).write_text(json.dumps(program_schema(), indent=2) + "\n")


if __name__ == "__main__":
    import sys

    write(*sys.argv[1:])
