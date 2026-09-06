"""Bounded project bundles for moving a shortlist between Luma sessions."""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from itertools import islice
from typing import Any, Iterable

from src.scenarios import Scenario, ScenarioError, ScenarioValidationError


PROJECT_SCHEMA_VERSION = 1
MAX_PROJECT_BYTES = 1_000_000
MAX_PROJECT_ITEMS = 50
_ROOT_FIELDS = {"schema_version", "source_snapshot", "scenarios"}


class ProjectError(ValueError):
    """A project document is unsafe, malformed, or incompatible."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProjectError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ProjectError(f"invalid JSON number: {value}")


def _bounded_scenarios(values: Iterable[Scenario]) -> tuple[Scenario, ...]:
    """Materialize at most one item beyond the contract limit.

    This protects direct Python callers as well as JSON imports from generators
    that are very large or never terminate.
    """
    try:
        scenarios = tuple(islice(iter(values), MAX_PROJECT_ITEMS + 1))
    except TypeError as exc:
        raise ProjectError("scenarios must be an iterable") from exc
    if len(scenarios) > MAX_PROJECT_ITEMS:
        raise ProjectError(f"project exceeds {MAX_PROJECT_ITEMS} scenarios")
    return scenarios


def _validate_snapshot(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ProjectError("source_snapshot must be a non-empty string of at most 128 characters")
    if value != value.strip():
        raise ProjectError("source_snapshot must not have leading or trailing whitespace")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ProjectError("source_snapshot contains control characters")
    return value


@dataclass(frozen=True)
class ProjectBundle:
    source_snapshot: str
    scenarios: tuple[Scenario, ...]
    schema_version: int = PROJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PROJECT_SCHEMA_VERSION:
            raise ProjectError(f"unsupported project schema version: {self.schema_version}")
        source_snapshot = _validate_snapshot(self.source_snapshot)
        scenarios = _bounded_scenarios(self.scenarios)
        if not scenarios:
            raise ProjectError("project must contain at least one scenario")
        if any(not isinstance(scenario, Scenario) for scenario in scenarios):
            raise ProjectError("every project item must be a validated scenario")
        object.__setattr__(self, "source_snapshot", source_snapshot)
        object.__setattr__(self, "scenarios", scenarios)

    @classmethod
    def create(cls, scenarios: Iterable[Scenario], source_snapshot: str) -> "ProjectBundle":
        return cls(source_snapshot=source_snapshot, scenarios=_bounded_scenarios(scenarios))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_snapshot": self.source_snapshot,
            "scenarios": [scenario.to_dict() for scenario in self.scenarios],
        }

    def to_json(self) -> str:
        try:
            document = json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, indent=2)
        except (TypeError, ValueError) as exc:
            raise ProjectError("project is not JSON serializable") from exc
        if len(document.encode("utf-8")) > MAX_PROJECT_BYTES:
            raise ProjectError(f"project exceeds {MAX_PROJECT_BYTES} encoded bytes")
        return document

    @classmethod
    def from_json(cls, document: str | bytes | bytearray) -> "ProjectBundle":
        if isinstance(document, str):
            if len(document) > MAX_PROJECT_BYTES:
                raise ProjectError(f"project must contain 1–{MAX_PROJECT_BYTES} encoded bytes")
            try:
                raw = document.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ProjectError("project must be valid UTF-8 text") from exc
        elif isinstance(document, bytes):
            raw = document
        elif isinstance(document, bytearray):
            if len(document) > MAX_PROJECT_BYTES:
                raise ProjectError(f"project must contain 1–{MAX_PROJECT_BYTES} encoded bytes")
            raw = bytes(document)
        else:
            raise ProjectError("project document must be text or bytes")
        if not raw or len(raw) > MAX_PROJECT_BYTES:
            raise ProjectError(f"project must contain 1–{MAX_PROJECT_BYTES} encoded bytes")
        try:
            parsed = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
        except UnicodeDecodeError as exc:
            raise ProjectError("project is not valid UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ProjectError("project is not valid JSON") from exc
        except RecursionError as exc:
            raise ProjectError("project JSON is too deeply nested") from exc
        if not isinstance(parsed, dict):
            raise ProjectError("project must be a JSON object")
        supplied = set(parsed)
        if supplied != _ROOT_FIELDS:
            missing = _ROOT_FIELDS.difference(supplied)
            extra = supplied.difference(_ROOT_FIELDS)
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(sorted(missing))}")
            if extra:
                details.append(f"unsupported fields: {', '.join(sorted(map(str, extra)))}")
            raise ProjectError("; ".join(details))
        raw_scenarios = parsed["scenarios"]
        if not isinstance(raw_scenarios, list):
            raise ProjectError("scenarios must be an array")
        if not 1 <= len(raw_scenarios) <= MAX_PROJECT_ITEMS:
            raise ProjectError(f"project must contain 1–{MAX_PROJECT_ITEMS} scenarios")
        try:
            scenarios = tuple(Scenario.from_dict(value) for value in raw_scenarios)
        except ScenarioError as exc:
            raise ProjectError(f"invalid scenario: {exc}") from exc
        try:
            return cls(
                schema_version=parsed["schema_version"],
                source_snapshot=parsed["source_snapshot"],
                scenarios=scenarios,
            )
        except ScenarioValidationError as exc:
            raise ProjectError(str(exc)) from exc
