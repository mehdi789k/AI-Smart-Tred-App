import json
from pathlib import Path

import pytest

from src.python.api.zmq_contract import (
    ContractValidationError,
    iter_contract_cases,
    load_contract_document,
    sha256_file,
    validate_envelope,
)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "zmq_contract_v1.json"
MQL5_CLIENT_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "mql5" / "ZmqClient.mqh"
)

EXPECTED_CASES = (
    ("heartbeat", "accepted", True, False, None),
    ("accepted_order", "accepted", True, False, None),
    ("rejected_order", "rejected", False, False, None),
    ("duplicate_message", "rejected", False, True, "case-duplicate-001"),
    ("timeout", "timeout", False, True, None),
    ("unknown_outcome", "unknown", False, True, None),
    ("invalid_envelope", "rejected", False, False, None),
    ("unsupported_schema", "rejected", False, False, None),
    ("dry_run", "accepted", True, False, None),
    ("reconciliation_evidence", "unknown", False, True, None),
    ("restart_state", "accepted", True, False, None),
    ("circuit_breaker", "rejected", False, False, None),
)


def test_contract_corpus_contains_required_cases():
    document = load_contract_document(FIXTURE_PATH)
    case_names = {case.name for case in document.cases}
    assert {
        "heartbeat",
        "accepted_order",
        "rejected_order",
        "duplicate_message",
        "timeout",
        "unknown_outcome",
        "invalid_envelope",
        "unsupported_schema",
        "dry_run",
    } <= case_names


def test_contract_document_matches_manifest_checksum():
    document = load_contract_document(FIXTURE_PATH)
    manifest_path = FIXTURE_PATH.with_name(f"{FIXTURE_PATH.stem}.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert document.schema_version == "1.0"
    assert manifest["schema_version"] == "1.0"
    assert manifest["case_count"] == len(document.cases)
    assert manifest["sha256"] == sha256_file(FIXTURE_PATH)


@pytest.mark.parametrize(
    (
        "case_name",
        "expected_status",
        "expected_accepted",
        "expected_reconciliation",
        "expected_duplicate_message_id",
    ),
    EXPECTED_CASES,
)
def test_required_cases_have_exact_expected_outcomes(
    case_name,
    expected_status,
    expected_accepted,
    expected_reconciliation,
    expected_duplicate_message_id,
):
    case = load_contract_document(FIXTURE_PATH).get_case(case_name)

    assert case.expected["status"] == expected_status
    assert case.expected["accepted"] is expected_accepted
    assert case.expected["requires_reconciliation"] is expected_reconciliation
    assert case.safety_invariant.strip()

    if expected_duplicate_message_id is None:
        assert "duplicate_message_id" not in case.expected
    else:
        assert case.expected["duplicate_message_id"] == expected_duplicate_message_id


def test_contract_loader_rejects_checksum_mismatch(tmp_path):
    bad_fixture = tmp_path / "zmq_contract_v1.json"
    bad_fixture.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8")[:-1], encoding="utf-8"
    )
    manifest_path = tmp_path / "zmq_contract_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_name": "zmq_contract_v1.json",
                "schema_version": "1.0",
                "case_count": 9,
                "sha256": sha256_file(FIXTURE_PATH),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="checksum mismatch"):
        load_contract_document(bad_fixture)


def test_validate_envelope_accepts_python_order_request():
    case = load_contract_document(FIXTURE_PATH).get_case("accepted_order")
    validate_envelope(case.request, source="python")


def test_mql5_make_result_serializes_required_accepted_field():
    source = MQL5_CLIENT_PATH.read_text(encoding="utf-8")

    make_result = source.split("string MakeResult(", 1)[1].split(
        "string MakeProtocolError(", 1
    )[0]

    assert '\\"payload\\":{\\"status\\":\\"%s\\",\\"accepted\\":%s,' in make_result
    assert 'accepted ? "true" : "false"' in make_result


def test_validate_envelope_rejects_unsupported_schema():
    case = load_contract_document(FIXTURE_PATH).get_case("accepted_order")
    envelope = dict(case.request)
    envelope["schema_version"] = "2.0"

    with pytest.raises(ContractValidationError, match="schema_version"):
        validate_envelope(envelope, source="python")


def test_unknown_case_requires_reconciliation():
    case = load_contract_document(FIXTURE_PATH).get_case("unknown_outcome")

    assert case.expected["status"] == "unknown"
    assert case.expected["accepted"] is False
    assert case.expected["requires_reconciliation"] is True


def test_iter_contract_cases_preserves_fixture_order():
    names = [case.name for case in iter_contract_cases(FIXTURE_PATH)]

    assert names == [expected[0] for expected in EXPECTED_CASES]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("message_id", "", "message_id"),
        ("source", "mt5", "source"),
        ("payload", [], "payload"),
    ],
)
def test_validate_envelope_rejects_invalid_fields(field, value, message):
    envelope = dict(
        load_contract_document(FIXTURE_PATH).get_case("accepted_order").request
    )
    envelope[field] = value

    with pytest.raises(ContractValidationError, match=message):
        validate_envelope(envelope, source="python")


def test_validate_envelope_rejects_unknown_fields():
    envelope = dict(
        load_contract_document(FIXTURE_PATH).get_case("accepted_order").request
    )
    envelope["unexpected"] = True

    with pytest.raises(ContractValidationError, match="unknown envelope field"):
        validate_envelope(envelope, source="python")


def test_contract_loader_rejects_manifest_metadata_mismatch(tmp_path):
    fixture_path = tmp_path / "zmq_contract_v1.json"
    fixture_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path = tmp_path / "zmq_contract_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_name": "other.json",
                "schema_version": "1.0",
                "case_count": 9,
                "sha256": sha256_file(fixture_path),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="fixture_name"):
        load_contract_document(fixture_path)


def test_contract_loader_rejects_manifest_case_count_mismatch(tmp_path):
    fixture_path = tmp_path / "zmq_contract_v1.json"
    fixture_path.write_text(FIXTURE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path = tmp_path / "zmq_contract_v1.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_name": fixture_path.name,
                "schema_version": "1.0",
                "case_count": 8,
                "sha256": sha256_file(fixture_path),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="case_count"):
        load_contract_document(fixture_path)


def test_contract_loader_rejects_duplicate_case_names(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["cases"].append(fixture["cases"][0])
    fixture_path = tmp_path / "zmq_contract_v1.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    (tmp_path / "zmq_contract_v1.manifest.json").write_text(
        json.dumps(
            {
                "fixture_name": fixture_path.name,
                "schema_version": "1.0",
                "case_count": 13,
                "sha256": sha256_file(fixture_path),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="duplicate case name"):
        load_contract_document(fixture_path)


def test_contract_loader_rejects_non_string_expected_status(tmp_path):
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture["cases"][0]["expected"]["status"] = []
    fixture_path = tmp_path / "zmq_contract_v1.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    (tmp_path / "zmq_contract_v1.manifest.json").write_text(
        json.dumps(
            {
                "fixture_name": fixture_path.name,
                "schema_version": "1.0",
                "case_count": len(fixture["cases"]),
                "sha256": sha256_file(fixture_path),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContractValidationError, match="expected status"):
        load_contract_document(fixture_path)
