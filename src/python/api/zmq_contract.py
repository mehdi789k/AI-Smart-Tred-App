"""ZeroMQ contract model and fixture loader for MT5 replay tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any

_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
_DEFAULT_FIXTURE = _FIXTURE_PATH / "zmq_contract_v1.json"
_SUPPORTED_SCHEMA_VERSION = "1.0"
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "message_id",
        "message_type",
        "sent_at",
        "correlation_id",
        "source",
        "payload",
    }
)
_CASE_FIELDS = frozenset({"name", "request", "expected", "safety_invariant"})
_EXPECTED_FIELDS = frozenset(
    {"status", "accepted", "requires_reconciliation", "duplicate_message_id"}
)
_EXPECTED_STATUSES = frozenset({"accepted", "rejected", "unknown", "timeout"})


class ContractValidationError(ValueError):
    """Raised when a contract fixture or inbound envelope is invalid."""


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 hex digest for a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ContractCase:
    """A single replay case extracted from the contract corpus."""

    name: str
    request: dict[str, Any]
    expected: dict[str, Any]
    safety_invariant: str


@dataclass(frozen=True)
class ContractDocument:
    """Versioned contract corpus for replay and validation tests."""

    schema_version: str
    cases: tuple[ContractCase, ...]

    def get_case(self, name: str) -> ContractCase:
        for case in self.cases:
            if case.name == name:
                return case
        raise KeyError(f"case {name!r} not found")


def validate_envelope(envelope: Mapping[str, Any], *, source: str) -> None:
    """Validate the strict Python-to-MT5 envelope contract."""
    if not isinstance(envelope, Mapping):
        raise ContractValidationError("envelope must be a mapping")
    if not isinstance(source, str) or not source.strip():
        raise ContractValidationError("source argument must be a non-empty string")

    missing_fields = sorted(_ENVELOPE_FIELDS - set(envelope))
    if missing_fields:
        formatted = ", ".join(missing_fields)
        raise ContractValidationError(f"missing envelope field(s): {formatted}")

    unknown_fields = sorted(set(envelope) - _ENVELOPE_FIELDS)
    if unknown_fields:
        formatted = ", ".join(str(field) for field in unknown_fields)
        raise ContractValidationError(f"unknown envelope field(s): {formatted}")

    schema_version = envelope["schema_version"]
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise ContractValidationError("schema_version must be '1.0'")

    for field_name in (
        "schema_version",
        "message_id",
        "message_type",
        "sent_at",
        "correlation_id",
    ):
        value = envelope[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ContractValidationError(f"{field_name} must be a non-empty string")

    source_value = envelope["source"]
    if source_value != source:
        raise ContractValidationError(f"source must be '{source}'")

    if not isinstance(envelope["payload"], dict):
        raise ContractValidationError("payload must be a JSON object")


def _read_json(path: Path, description: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractValidationError(f"{description} missing: {path}") from exc
    except (OSError, UnicodeDecodeError, JSONDecodeError) as exc:
        raise ContractValidationError(f"invalid {description}: {path}") from exc


def _validate_expected(case_name: str, expected: object) -> dict[str, Any]:
    if not isinstance(expected, dict):
        raise ContractValidationError(f"case {case_name!r} expected must be an object")
    unknown_fields = sorted(set(expected) - _EXPECTED_FIELDS)
    if unknown_fields:
        raise ContractValidationError(
            f"case {case_name!r} has unknown expected field(s): "
            f"{', '.join(str(field) for field in unknown_fields)}"
        )
    required_fields = {"status", "accepted", "requires_reconciliation"}
    missing_fields = sorted(required_fields - set(expected))
    if missing_fields:
        raise ContractValidationError(
            f"case {case_name!r} expected missing field(s): {', '.join(missing_fields)}"
        )

    status = expected["status"]
    if not isinstance(status, str) or status not in _EXPECTED_STATUSES:
        raise ContractValidationError(
            f"case {case_name!r} expected status is unsupported"
        )
    for field_name in ("accepted", "requires_reconciliation"):
        if not isinstance(expected[field_name], bool):
            raise ContractValidationError(
                f"case {case_name!r} expected {field_name} must be a boolean"
            )
    duplicate_message_id = expected.get("duplicate_message_id")
    if duplicate_message_id is not None and (
        not isinstance(duplicate_message_id, str) or not duplicate_message_id.strip()
    ):
        raise ContractValidationError(
            f"case {case_name!r} duplicate_message_id must be a non-empty string"
        )
    return expected


def load_contract_document(path: str | Path = _DEFAULT_FIXTURE) -> ContractDocument:
    """Load a contract corpus and verify its manifest and schema."""
    fixture_path = Path(path).resolve()
    manifest_path = fixture_path.with_name(f"{fixture_path.stem}.manifest.json")
    manifest = _read_json(manifest_path, "contract manifest")
    if not isinstance(manifest, dict):
        raise ContractValidationError("contract manifest must be an object")
    manifest_fields = {"fixture_name", "schema_version", "case_count", "sha256"}
    if set(manifest) != manifest_fields:
        raise ContractValidationError(
            "manifest fields do not match the contract schema"
        )
    if manifest["fixture_name"] != fixture_path.name:
        raise ContractValidationError("manifest fixture_name does not match fixture")
    if manifest["schema_version"] != _SUPPORTED_SCHEMA_VERSION:
        raise ContractValidationError("manifest schema_version is unsupported")
    if not isinstance(manifest["case_count"], int) or isinstance(
        manifest["case_count"], bool
    ):
        raise ContractValidationError("manifest case_count must be an integer")
    if (
        not isinstance(manifest["sha256"], str)
        or len(manifest["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in manifest["sha256"])
    ):
        raise ContractValidationError("manifest sha256 must be a lowercase hex digest")

    try:
        actual_checksum = sha256_file(fixture_path)
    except OSError as exc:
        raise ContractValidationError(
            f"fixture missing or unreadable: {fixture_path}"
        ) from exc
    if manifest["sha256"] != actual_checksum:
        raise ContractValidationError(
            f"checksum mismatch for {fixture_path.name}: "
            f"expected {manifest['sha256']}, got {actual_checksum}"
        )

    document = _read_json(fixture_path, "contract fixture")
    if not isinstance(document, dict):
        raise ContractValidationError("contract document must be an object")
    if set(document) != {"schema_version", "cases"}:
        raise ContractValidationError(
            "document fields do not match the contract schema"
        )
    schema_version = document["schema_version"]
    if schema_version != manifest["schema_version"]:
        raise ContractValidationError("document schema_version does not match manifest")
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise ContractValidationError("unsupported contract schema_version")

    raw_cases = document["cases"]
    if not isinstance(raw_cases, list):
        raise ContractValidationError("document cases must be a list")
    if len(raw_cases) != manifest["case_count"]:
        raise ContractValidationError("manifest case_count does not match fixture")

    cases: list[ContractCase] = []
    case_names: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ContractValidationError("each case must be an object")
        if set(raw_case) != _CASE_FIELDS:
            raise ContractValidationError(
                "case fields do not match the contract schema"
            )
        name = raw_case["name"]
        if not isinstance(name, str) or not name.strip():
            raise ContractValidationError("case name is required")
        if name in case_names:
            raise ContractValidationError(f"duplicate case name: {name}")
        case_names.add(name)

        request = raw_case["request"]
        if not isinstance(request, dict):
            raise ContractValidationError(f"case {name!r} request must be an object")
        expected = _validate_expected(name, raw_case["expected"])
        safety_invariant = raw_case["safety_invariant"]
        if not isinstance(safety_invariant, str) or not safety_invariant.strip():
            raise ContractValidationError(
                f"case {name!r} safety_invariant must be a non-empty string"
            )
        if name not in {"invalid_envelope", "unsupported_schema"}:
            validate_envelope(request, source="python")
        cases.append(
            ContractCase(
                name=name,
                request=request,
                expected=expected,
                safety_invariant=safety_invariant,
            )
        )

    return ContractDocument(schema_version=schema_version, cases=tuple(cases))


def iter_contract_cases(path: str | Path = _DEFAULT_FIXTURE) -> Iterator[ContractCase]:
    """Yield each contract case from the versioned corpus."""
    yield from load_contract_document(path).cases
