"""
Unit tests for candle_validator module.

Validates fail-closed enforcement of schema versioning and ingestion provenance.
"""

import pytest

from src.python.indicators.candle_validator import (
    CandleValidationError,
    validate_candle_batch_versioned,
    validate_candle_versioned,
)
from tests.python.indicators.candle_fixtures import versioned_candles


class TestValidateCandle:
    """Tests for single-candle validation."""

    def test_valid_candle_passes(self):
        """Valid candle with all required fields passes validation."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "received_at": "2024-01-01T00:00:00Z",
                "collector_version": "1.0.0",
            },
        }
        result = validate_candle_versioned(candle)
        assert result["schema_version"] == 1
        assert "ingestion_metadata" in result

    def test_missing_schema_version_raises_error(self):
        """Legacy unversioned candle (missing schema_version) is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "received_at": "2024-01-01T00:00:00Z",
                "collector_version": "1.0.0",
            },
        }
        with pytest.raises(CandleValidationError, match="schema_version"):
            validate_candle_versioned(candle)

    def test_non_numeric_schema_version_raises_error(self):
        """Non-numeric schema_version (e.g., string) is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": "1",  # String, not int
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "received_at": "2024-01-01T00:00:00Z",
                "collector_version": "1.0.0",
            },
        }
        with pytest.raises(CandleValidationError, match="numeric"):
            validate_candle_versioned(candle)

    def test_mismatched_schema_version_raises_error(self):
        """Schema version not matching CURRENT_SCHEMA_VERSION is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 2,  # Wrong version
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "received_at": "2024-01-01T00:00:00Z",
                "collector_version": "1.0.0",
            },
        }
        with pytest.raises(CandleValidationError, match="version mismatch"):
            validate_candle_versioned(candle)

    def test_missing_ingestion_metadata_raises_error(self):
        """Candle without ingestion_metadata is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
        }
        with pytest.raises(CandleValidationError, match="ingestion_metadata"):
            validate_candle_versioned(candle)

    def test_missing_ingestion_id_raises_error(self):
        """Ingestion metadata missing ingestion_id is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
            "ingestion_metadata": {
                "received_at": "2024-01-01T00:00:00Z",
                "collector_version": "1.0.0",
            },
        }
        with pytest.raises(CandleValidationError, match="ingestion_id"):
            validate_candle_versioned(candle)

    def test_missing_received_at_raises_error(self):
        """Ingestion metadata missing received_at is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "collector_version": "1.0.0",
            },
        }
        with pytest.raises(CandleValidationError, match="received_at"):
            validate_candle_versioned(candle)

    def test_missing_collector_version_raises_error(self):
        """Ingestion metadata missing collector_version is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
            "ingestion_metadata": {
                "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                "received_at": "2024-01-01T00:00:00Z",
            },
        }
        with pytest.raises(CandleValidationError, match="collector_version"):
            validate_candle_versioned(candle)

    def test_non_dict_ingestion_metadata_raises_error(self):
        """ingestion_metadata that is not a dict is rejected."""
        candle = {
            "open": 1.0500,
            "high": 1.0650,
            "low": 1.0450,
            "close": 1.0600,
            "volume": 1000,
            "time": 1609459200,
            "schema_version": 1,
            "source": "mt5",
            "ingestion_metadata": "not_a_dict",  # String instead of dict
        }
        with pytest.raises(CandleValidationError, match="dict"):
            validate_candle_versioned(candle)

    def test_non_dict_candle_input_raises_error(self):
        """Non-dict candle input raises error."""
        with pytest.raises(CandleValidationError, match="dict"):
            validate_candle_versioned("not_a_dict")

    def test_list_input_raises_error(self):
        """List input (should use batch validator) raises error."""
        with pytest.raises(CandleValidationError, match="dict"):
            validate_candle_versioned([])


class TestValidateCandleBatch:
    """Tests for batch-candle validation with fail-closed behavior."""

    def test_valid_candle_batch_passes(self):
        """Batch of all valid candles passes."""
        candles = [
            {
                "open": 1.0500 + i * 0.001,
                "high": 1.0650 + i * 0.001,
                "low": 1.0450 + i * 0.001,
                "close": 1.0600 + i * 0.001,
                "volume": 1000,
                "time": 1609459200 + i * 60,
                "schema_version": 1,
                "source": "mt5",
                "ingestion_metadata": {
                    "ingestion_id": f"123e4567-e89b-12d3-a456-42661417400{i}",
                    "received_at": "2024-01-01T00:00:00Z",
                    "collector_version": "1.0.0",
                },
            }
            for i in range(3)
        ]
        result = validate_candle_batch_versioned(candles)
        assert len(result) == 3
        assert all(c["schema_version"] == 1 for c in result)

    def test_empty_batch_passes(self):
        """Empty candle list passes."""
        result = validate_candle_batch_versioned([])
        assert result == []

    def test_batch_stops_on_first_invalid_candle(self):
        """Batch validation stops on first invalid candle (fail-closed)."""
        candles = [
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459200,
                "schema_version": 1,
                "source": "mt5",
                "ingestion_metadata": {
                    "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                    "received_at": "2024-01-01T00:00:00Z",
                    "collector_version": "1.0.0",
                },
            },
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459201,
                # Missing schema_version
            },
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459202,
                "schema_version": 1,
                "source": "mt5",
                "ingestion_metadata": {
                    "ingestion_id": "123e4567-e89b-12d3-a456-426614174002",
                    "received_at": "2024-01-01T00:00:00Z",
                    "collector_version": "1.0.0",
                },
            },
        ]
        with pytest.raises(CandleValidationError, match="index 1"):
            validate_candle_batch_versioned(candles)

    def test_batch_error_includes_index_information(self):
        """Batch validation error includes index of failing candle."""
        candles = [
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459200,
                "schema_version": 1,
                "source": "mt5",
                "ingestion_metadata": {
                    "ingestion_id": "123e4567-e89b-12d3-a456-426614174000",
                    "received_at": "2024-01-01T00:00:00Z",
                    "collector_version": "1.0.0",
                },
            },
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459201,
                "schema_version": 1,
                "source": "mt5",
                "ingestion_metadata": {
                    "ingestion_id": "123e4567-e89b-12d3-a456-426614174001",
                    "received_at": "2024-01-01T00:00:00Z",
                    "collector_version": "1.0.0",
                },
            },
            {
                "open": 1.0500,
                "high": 1.0650,
                "low": 1.0450,
                "close": 1.0600,
                "volume": 1000,
                "time": 1609459202,
                # Missing schema_version at index 2
            },
        ]
        with pytest.raises(CandleValidationError) as exc_info:
            validate_candle_batch_versioned(candles)
        assert "index 2" in str(exc_info.value)

    def test_batch_with_first_candle_invalid(self):
        """Batch validation fails immediately if first candle is invalid."""
        candles = [
            {"volume": 1000}  # Minimal invalid candle
        ]
        with pytest.raises(CandleValidationError, match="index 0"):
            validate_candle_batch_versioned(candles)

    def test_batch_with_all_invalid_candles(self):
        """Batch with all invalid candles fails on first (index 0)."""
        candles = [{"volume": 1000}, {"volume": 2000}, {"volume": 3000}]
        with pytest.raises(CandleValidationError, match="index 0"):
            validate_candle_batch_versioned(candles)


class TestCandleValidationError:
    """Tests for CandleValidationError exception."""

    def test_error_is_exception(self):
        """CandleValidationError is an Exception."""
        error = CandleValidationError("test message")
        assert isinstance(error, Exception)

    def test_error_message_preserved(self):
        """Error message is preserved in exception."""
        message = "Invalid candle: missing schema_version"
        error = CandleValidationError(message)
        assert str(error) == message


def test_migration_adds_schema_version_and_ingestion_metadata():
    """Legacy candle migration fills the mandatory schema and provenance fields."""
    from scripts.migrate_candles_to_versioned import migrate_candle_to_versioned

    candle = {
        "open": 1.0500,
        "high": 1.0650,
        "low": 1.0450,
        "close": 1.0600,
        "volume": 1000,
        "time": 1609459200,
    }

    result = migrate_candle_to_versioned(
        candle, source="mt5_official", collector_version="1.2.3"
    )

    assert result["schema_version"] == 1
    assert result["source"] == "mt5_official"
    assert result["ingestion_metadata"]["collector_version"] == "1.2.3"
    assert "ingestion_id" in result["ingestion_metadata"]
    assert "received_at" in result["ingestion_metadata"]


def test_indicator_calculation_rejects_unversioned_candles():
    from src.python.indicators.adx import calculate_adx

    candle = {
        "open": 1.05,
        "high": 1.065,
        "low": 1.045,
        "close": 1.06,
        "volume": 1000,
        "time": 1609459200,
    }

    with pytest.raises(CandleValidationError, match="schema_version"):
        calculate_adx([candle])


def test_validator_rejects_missing_source_and_invalid_ohlc():
    candle = versioned_candles([{"open": 10, "high": 12, "low": 9, "close": 11}])[0]
    candle.pop("source")
    with pytest.raises(CandleValidationError, match="source"):
        validate_candle_versioned(candle)

    candle = versioned_candles([{"open": 10, "high": 12, "low": 9, "close": 11}])[0]
    candle["low"] = 10.5
    with pytest.raises(CandleValidationError, match="OHLC bounds"):
        validate_candle_versioned(candle)


def test_validator_rejects_naive_provenance_timestamp():
    candle = versioned_candles([{"open": 10, "high": 12, "low": 9, "close": 11}])[0]
    candle["ingestion_metadata"]["received_at"] = "2026-01-01T00:00:00"
    with pytest.raises(CandleValidationError, match="timezone-aware UTC"):
        validate_candle_versioned(candle)


def test_batch_rejects_duplicate_or_out_of_order_timestamps():
    candles = versioned_candles(
        [
            {
                "timestamp": "2026-01-01T00:01:00Z",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
            },
            {
                "timestamp": "2026-01-01T00:01:00Z",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
            },
        ]
    )
    with pytest.raises(CandleValidationError, match="strictly ascending"):
        validate_candle_batch_versioned(candles)


def test_filter_calculation_rejects_unversioned_candles():
    from src.python.filters.fomo_filters import calculate_fomo_filter

    with pytest.raises(CandleValidationError, match="schema_version"):
        calculate_fomo_filter([{"close": 100}], ["buy"], [100], [200])
