"""Versioned, bounded serialization for reproducible assessment scenarios.

The contract deliberately contains only user-supplied assessment inputs and
explicit overrides.  Catalogue observations are referred to by ``product_id``
and ``source_snapshot`` instead of being copied into a share URL.

URL tokens carry a checksum so truncated or edited values fail closed.  An
optional integrity key upgrades that checksum to an HMAC when tokens cross a
trust boundary.  Unkeyed tokens detect corruption, but are not authentication.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from itertools import islice
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping, Union


SCENARIO_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCENARIO_SCHEMA_VERSION})
TOKEN_PREFIX = "luma-s1"

# The token limit stays below common practical URL limits.  The uncompressed
# limit is enforced during streaming decompression to make compression bombs
# harmless.
MAX_TOKEN_CHARS = 4096
MAX_JSON_BYTES = 16_384
MAX_COMPRESSED_BYTES = 3072
INTEGRITY_TAG_BYTES = 16


ScenarioValue = Union[str, float, bool, None]


class ScenarioError(ValueError):
    """Base class for scenario contract failures."""


class ScenarioValidationError(ScenarioError):
    """The supplied scenario does not satisfy the current schema."""


class ScenarioVersionError(ScenarioValidationError):
    """The scenario uses a schema version this application cannot read."""


class ScenarioTokenError(ScenarioError):
    """A URL token is malformed or exceeds a safety limit."""


class ScenarioIntegrityError(ScenarioTokenError):
    """A URL token's integrity tag does not match its payload."""


@dataclass(frozen=True)
class _FieldSpec:
    kind: str
    nullable: bool = True
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    choices: frozenset[str] | None = None


_TEXT = "text"
_NUMBER = "number"
_BOOLEAN = "boolean"

# This is the union of the public assessment request, UI lifecycle controls,
# and legacy inputs still consumed by calculate_assessment.  Keeping the list
# explicit prevents arbitrary JSON from entering application/session state.
ASSESSMENT_FIELD_SPECS: Mapping[str, _FieldSpec] = MappingProxyType(
    {
        "query": _FieldSpec(_TEXT, max_length=120),
        "name": _FieldSpec(_TEXT, nullable=False, max_length=320),
        "manufacturer": _FieldSpec(_TEXT, nullable=False, max_length=160),
        "category": _FieldSpec(_TEXT, nullable=False, max_length=80),
        "manufacturing_kg": _FieldSpec(_NUMBER, minimum=0, maximum=5000),
        "active_power_w": _FieldSpec(_NUMBER, minimum=0, maximum=10_000),
        "daily_hours": _FieldSpec(_NUMBER, minimum=0, maximum=24),
        "annual_energy_kwh": _FieldSpec(_NUMBER, minimum=0, maximum=100_000),
        "grid_kg_co2_per_kwh": _FieldSpec(_NUMBER, nullable=False, minimum=0, maximum=2),
        "grid_profile": _FieldSpec(
            _TEXT,
            max_length=32,
            choices=frozenset({"Low-carbon", "Average", "Coal-heavy"}),
        ),
        "lifespan_years": _FieldSpec(_NUMBER, minimum=0.5, maximum=30),
        "repairability": _FieldSpec(_NUMBER, minimum=0, maximum=10),
        "recyclability_pct": _FieldSpec(_NUMBER, minimum=0, maximum=100),
        "recycled_content_pct": _FieldSpec(_NUMBER, minimum=0, maximum=100),
        "battery_wh": _FieldSpec(_NUMBER, minimum=0, maximum=10_000),
        "replaceable_battery": _FieldSpec(_BOOLEAN),
        "weight_kg": _FieldSpec(_NUMBER, minimum=0, maximum=500),
        "transport_km": _FieldSpec(_NUMBER, minimum=0, maximum=50_000),
        "software_support_years": _FieldSpec(_NUMBER, minimum=0, maximum=50),
        "brand_repair_success_rate": _FieldSpec(_NUMBER, minimum=0, maximum=1),
        "reported_lifecycle_kg": _FieldSpec(_NUMBER, minimum=0, maximum=100_000),
    }
)
ASSESSMENT_INPUT_FIELDS = frozenset(ASSESSMENT_FIELD_SPECS)
IDENTITY_FIELDS = frozenset({"query", "name", "manufacturer", "category"})
ASSESSMENT_OVERRIDE_FIELDS = ASSESSMENT_INPUT_FIELDS.difference(IDENTITY_FIELDS)

# These values describe the user's assessment context rather than the product.
# They are the only saved inputs that may be overlaid onto a currently matched
# catalogue row without changing the row's identity or observed provenance.
CATALOG_CONTEXT_FIELDS = frozenset(
    {
        "daily_hours",
        "grid_kg_co2_per_kwh",
        "grid_profile",
    }
)

_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "product_id",
        "region",
        "theme",
        "source_snapshot",
        "model_version",
        "scoring_version",
        "inputs",
        "overrides",
    }
)
_TOKEN_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_INTEGRITY_DOMAIN = b"luma-scenario-token\x00"
_UNSAFE_TEXT_CHARACTERS = frozenset(
    {
        "\u200b",  # zero-width space
        "\u202a",  # bidirectional embedding/override controls
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
        "\ufeff",  # byte-order mark / zero-width no-break space
    }
)


def _fail(message: str) -> None:
    raise ScenarioValidationError(message)


def _validate_text(value: Any, name: str, *, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(f"{name} must be a string")
    if not allow_empty and not value.strip():
        _fail(f"{name} must not be empty")
    if not allow_empty and value != value.strip():
        _fail(f"{name} must not have leading or trailing whitespace")
    if len(value) > max_length:
        _fail(f"{name} exceeds {max_length} characters")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"} or character in _UNSAFE_TEXT_CHARACTERS
        for character in value
    ):
        _fail(f"{name} contains unsafe control characters")
    return value


def _validate_field_value(name: str, value: Any) -> ScenarioValue:
    spec = ASSESSMENT_FIELD_SPECS[name]
    if value is None:
        if not spec.nullable:
            _fail(f"{name} must not be null")
        return None
    if spec.kind == _TEXT:
        text = _validate_text(
            value,
            name,
            max_length=spec.max_length or 160,
            allow_empty=spec.nullable,
        )
        if spec.choices is not None and text not in spec.choices:
            _fail(f"{name} is not a supported value")
        return text
    if spec.kind == _BOOLEAN:
        if type(value) is not bool:
            _fail(f"{name} must be a boolean or null")
        return value
    if isinstance(value, bool) or not isinstance(value, Real):
        _fail(f"{name} must be a number or null")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ScenarioValidationError(f"{name} must be a finite number or null") from exc
    if not math.isfinite(number):
        _fail(f"{name} must be finite")
    if spec.minimum is not None and number < spec.minimum:
        _fail(f"{name} must be at least {spec.minimum:g}")
    if spec.maximum is not None and number > spec.maximum:
        _fail(f"{name} must be at most {spec.maximum:g}")
    return number


def _validate_values(values: Any, *, overrides: bool) -> Mapping[str, ScenarioValue]:
    label = "overrides" if overrides else "inputs"
    if not isinstance(values, Mapping):
        _fail(f"{label} must be an object")
    allowed = ASSESSMENT_OVERRIDE_FIELDS if overrides else ASSESSMENT_INPUT_FIELDS
    normalized: dict[str, ScenarioValue] = {}
    items = list(islice(values.items(), len(allowed) + 1))
    if len(items) > len(allowed):
        _fail(f"{label} contains too many fields")
    for key, value in items:
        if not isinstance(key, str):
            _fail(f"{label} keys must be strings")
        if key not in allowed:
            _fail(f"unsupported {label} field: {key}")
        normalized[key] = _validate_field_value(key, value)
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class Scenario:
    """Portable inputs and provenance needed to reopen one assessment."""

    inputs: Mapping[str, ScenarioValue]
    source_snapshot: str
    model_version: str
    scoring_version: str
    overrides: Mapping[str, ScenarioValue] = field(default_factory=dict)
    product_id: str | None = None
    region: str = "India"
    theme: str = "light"
    schema_version: int = SCENARIO_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            _fail("schema_version must be an integer")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ScenarioVersionError(f"unsupported scenario schema version: {self.schema_version}")
        product_id = self.product_id
        if product_id is not None:
            product_id = _validate_text(product_id, "product_id", max_length=200)
        region = _validate_text(self.region, "region", max_length=120)
        theme = _validate_text(self.theme, "theme", max_length=8)
        if theme not in {"light", "dark"}:
            _fail("theme must be 'light' or 'dark'")
        source_snapshot = _validate_text(self.source_snapshot, "source_snapshot", max_length=128)
        model_version = _validate_text(self.model_version, "model_version", max_length=64)
        scoring_version = _validate_text(self.scoring_version, "scoring_version", max_length=64)
        inputs = _validate_values(self.inputs, overrides=False)
        overrides = _validate_values(self.overrides, overrides=True)

        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "theme", theme)
        object.__setattr__(self, "source_snapshot", source_snapshot)
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "scoring_version", scoring_version)
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "overrides", overrides)

    @classmethod
    def create(
        cls,
        *,
        inputs: Mapping[str, ScenarioValue],
        source_snapshot: str,
        model_version: str,
        scoring_version: str,
        overrides: Mapping[str, ScenarioValue] | None = None,
        product_id: str | None = None,
        region: str = "India",
        theme: str = "light",
    ) -> "Scenario":
        return cls(
            inputs=inputs,
            overrides={} if overrides is None else overrides,
            product_id=product_id,
            region=region,
            theme=theme,
            source_snapshot=source_snapshot,
            model_version=model_version,
            scoring_version=scoring_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "region": self.region,
            "theme": self.theme,
            "source_snapshot": self.source_snapshot,
            "model_version": self.model_version,
            "scoring_version": self.scoring_version,
            "inputs": dict(self.inputs),
            "overrides": dict(self.overrides),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Scenario":
        if not isinstance(value, Mapping):
            _fail("scenario must be an object")
        supplied = set(islice(iter(value), len(_ROOT_FIELDS) + 1))
        missing = _ROOT_FIELDS.difference(supplied)
        extra = supplied.difference(_ROOT_FIELDS)
        if missing:
            _fail(f"scenario is missing fields: {', '.join(sorted(missing))}")
        if extra:
            _fail(f"scenario has unsupported fields: {', '.join(sorted(map(str, extra)))}")
        return cls(
            schema_version=value["schema_version"],
            product_id=value["product_id"],
            region=value["region"],
            theme=value["theme"],
            source_snapshot=value["source_snapshot"],
            model_version=value["model_version"],
            scoring_version=value["scoring_version"],
            inputs=value["inputs"],
            overrides=value["overrides"],
        )

    def assessment_payload(self) -> dict[str, ScenarioValue]:
        """Return API-ready fallback inputs, with ``product_id`` when present.

        This is intended for the assessment API, which owns catalogue matching.
        UI code that already matched a live catalogue row must use
        :meth:`catalog_payload` instead so saved fallback observations cannot
        impersonate current-source data.
        """
        payload = self.fallback_payload()
        if self.product_id is not None:
            payload["product_id"] = self.product_id  # type: ignore[assignment]
        return payload

    def catalog_payload(self) -> dict[str, ScenarioValue]:
        """Return values safe to overlay onto a live matched catalogue row.

        Always-safe user context is retained, and explicit overrides are
        applied last.  Saved product identity and non-overridden lifecycle
        observations are deliberately excluded so the live row remains the
        authority for provenance.
        """
        payload = {
            key: value
            for key, value in self.inputs.items()
            if key in CATALOG_CONTEXT_FIELDS
        }
        payload.update(self.overrides)
        return payload

    def fallback_payload(self) -> dict[str, ScenarioValue]:
        """Return all saved effective inputs for an absent/unlisted product.

        Call this only when no current catalogue row was matched.  It preserves
        the portable snapshot so an assessment can still be reconstructed, but
        callers must not label those values as observations from today's
        catalogue snapshot.
        """
        payload = dict(self.inputs)
        payload.update(self.overrides)
        return payload

    def to_token(self, *, integrity_key: bytes | str | None = None) -> str:
        return encode_scenario(self, integrity_key=integrity_key)

    @classmethod
    def from_token(cls, token: str, *, integrity_key: bytes | str | None = None) -> "Scenario":
        return decode_scenario(token, integrity_key=integrity_key)

    def to_json(self, *, pretty: bool = True) -> str:
        return export_scenario_json(self, pretty=pretty)

    @classmethod
    def from_json(cls, document: str | bytes | bytearray) -> "Scenario":
        return import_scenario_json(document)


def scenario_to_dict(scenario: Scenario) -> dict[str, Any]:
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be a Scenario")
    return scenario.to_dict()


def scenario_from_dict(value: Mapping[str, Any]) -> Scenario:
    return Scenario.from_dict(value)


def _canonical_json(scenario: Scenario) -> bytes:
    try:
        encoded = json.dumps(
            scenario_to_dict(scenario),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:  # Defensive: validation should catch this first.
        raise ScenarioValidationError("scenario is not JSON serializable") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise ScenarioValidationError(f"scenario exceeds {MAX_JSON_BYTES} encoded bytes")
    return encoded


def _normalize_integrity_key(integrity_key: bytes | str | None) -> bytes | None:
    if integrity_key is None:
        return None
    if isinstance(integrity_key, str):
        try:
            key = integrity_key.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("integrity_key must be valid UTF-8 text") from exc
    elif isinstance(integrity_key, bytes):
        key = integrity_key
    else:
        raise TypeError("integrity_key must be bytes, str, or None")
    if len(key) < 16:
        raise ValueError("integrity_key must contain at least 16 bytes")
    if len(key) > 1024:
        raise ValueError("integrity_key exceeds 1024 bytes")
    return key


def _integrity_tag(compressed: bytes, integrity_key: bytes | str | None) -> bytes:
    key = _normalize_integrity_key(integrity_key)
    material = _INTEGRITY_DOMAIN + TOKEN_PREFIX.encode("ascii") + b"\x00" + compressed
    digest = hashlib.sha256(material).digest() if key is None else hmac.new(key, material, hashlib.sha256).digest()
    return digest[:INTEGRITY_TAG_BYTES]


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(segment: str, *, name: str) -> bytes:
    if not segment or _TOKEN_SEGMENT.fullmatch(segment) is None:
        raise ScenarioTokenError(f"invalid {name} encoding")
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.b64decode(segment + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ScenarioTokenError(f"invalid {name} encoding") from exc


def encode_scenario(scenario: Scenario, *, integrity_key: bytes | str | None = None) -> str:
    """Encode a scenario as a compact URL-query-safe token."""
    raw = _canonical_json(scenario)
    compressed = zlib.compress(raw, level=9)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ScenarioValidationError("compressed scenario exceeds the token payload limit")
    tag = _integrity_tag(compressed, integrity_key)
    token = f"{TOKEN_PREFIX}.{_b64encode(compressed)}.{_b64encode(tag)}"
    if len(token) > MAX_TOKEN_CHARS:
        raise ScenarioValidationError(f"scenario token exceeds {MAX_TOKEN_CHARS} characters")
    return token


def _bounded_decompress(compressed: bytes) -> bytes:
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ScenarioTokenError("compressed scenario exceeds the token payload limit")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(compressed, MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES or decompressor.unconsumed_tail:
            raise ScenarioTokenError("scenario expands beyond the JSON size limit")
        raw += decompressor.flush(MAX_JSON_BYTES - len(raw) + 1)
    except zlib.error as exc:
        raise ScenarioTokenError("scenario payload is not valid compressed data") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise ScenarioTokenError("scenario expands beyond the JSON size limit")
    if not decompressor.eof or decompressor.unused_data:
        raise ScenarioTokenError("scenario payload has an invalid compressed boundary")
    return raw


def _reject_constant(value: str) -> None:
    raise ScenarioValidationError(f"invalid JSON number: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScenarioValidationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _parse_json(raw: bytes) -> Scenario:
    try:
        document = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScenarioValidationError("scenario JSON must be UTF-8") from exc
    try:
        value = json.loads(
            document,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_without_duplicates,
        )
    except ScenarioValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ScenarioValidationError("scenario JSON is malformed") from exc
    return scenario_from_dict(value)


def decode_scenario(token: str, *, integrity_key: bytes | str | None = None) -> Scenario:
    """Verify and decode a URL token, failing closed on any mismatch."""
    if not isinstance(token, str):
        raise TypeError("token must be a string")
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise ScenarioTokenError(f"scenario token must contain at most {MAX_TOKEN_CHARS} characters")
    try:
        prefix, payload_segment, tag_segment = token.split(".")
    except ValueError as exc:
        raise ScenarioTokenError("scenario token must have three segments") from exc
    if prefix != TOKEN_PREFIX:
        raise ScenarioVersionError(f"unsupported scenario token version: {prefix}")
    compressed = _b64decode(payload_segment, name="payload")
    supplied_tag = _b64decode(tag_segment, name="integrity tag")
    if len(supplied_tag) != INTEGRITY_TAG_BYTES:
        raise ScenarioIntegrityError("scenario token has an invalid integrity tag")
    expected_tag = _integrity_tag(compressed, integrity_key)
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise ScenarioIntegrityError("scenario token failed integrity validation")
    return _parse_json(_bounded_decompress(compressed))


def export_scenario_json(scenario: Scenario, *, pretty: bool = True) -> str:
    """Export the same versioned contract as a portable JSON document."""
    raw = _canonical_json(scenario)
    if not pretty:
        return raw.decode("utf-8")
    return json.dumps(
        scenario_to_dict(scenario),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def import_scenario_json(document: str | bytes | bytearray) -> Scenario:
    """Import a bounded UTF-8 JSON scenario with duplicate-key rejection."""
    if isinstance(document, str):
        # UTF-8 never encodes a Unicode scalar in fewer than one byte, so this
        # rejects oversized text without first allocating another large copy.
        if len(document) > MAX_JSON_BYTES:
            raise ScenarioValidationError(f"scenario JSON exceeds {MAX_JSON_BYTES} bytes")
        try:
            raw = document.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ScenarioValidationError("scenario JSON must be valid UTF-8 text") from exc
    elif isinstance(document, bytes):
        raw = document
    elif isinstance(document, bytearray):
        if len(document) > MAX_JSON_BYTES:
            raise ScenarioValidationError(f"scenario JSON exceeds {MAX_JSON_BYTES} bytes")
        raw = bytes(document)
    else:
        raise TypeError("scenario JSON must be str, bytes, or bytearray")
    if len(raw) > MAX_JSON_BYTES:
        raise ScenarioValidationError(f"scenario JSON exceeds {MAX_JSON_BYTES} bytes")
    return _parse_json(raw)


# Explicit URL-oriented aliases make call sites self-documenting.
encode_scenario_token = encode_scenario
decode_scenario_token = decode_scenario


__all__ = [
    "ASSESSMENT_FIELD_SPECS",
    "ASSESSMENT_INPUT_FIELDS",
    "ASSESSMENT_OVERRIDE_FIELDS",
    "CATALOG_CONTEXT_FIELDS",
    "INTEGRITY_TAG_BYTES",
    "MAX_JSON_BYTES",
    "MAX_TOKEN_CHARS",
    "SCENARIO_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "TOKEN_PREFIX",
    "Scenario",
    "ScenarioError",
    "ScenarioIntegrityError",
    "ScenarioTokenError",
    "ScenarioValidationError",
    "ScenarioVersionError",
    "decode_scenario",
    "decode_scenario_token",
    "encode_scenario",
    "encode_scenario_token",
    "export_scenario_json",
    "import_scenario_json",
    "scenario_from_dict",
    "scenario_to_dict",
]
