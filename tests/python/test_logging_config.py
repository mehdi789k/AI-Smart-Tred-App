import json
import logging

from src.python.logging_config import _safe_value, _StructuredFormatter, log_event


def test_safe_value_redacts_credentials_recursively():
    value = _safe_value(
        {"password": "secret", "nested": {"api_key": "abc"}, "message": "Bearer xyz"}
    )

    assert value["password"] == "[REDACTED]"
    assert value["nested"]["api_key"] == "[REDACTED]"
    assert value["message"] == "Bearer [REDACTED]"


def test_log_event_emits_domain_fields_as_json():
    record = None
    logger = logging.getLogger("test.smart")
    logger.setLevel(logging.INFO)

    class Capture(logging.Handler):
        def emit(self, item):
            nonlocal record
            record = item

    handler = Capture()
    logger.addHandler(handler)
    try:
        log_event(logger, logging.INFO, "cycle_skipped", reason="daily_loss_limit")
        payload = json.loads(_StructuredFormatter().format(record))
    finally:
        logger.removeHandler(handler)

    assert payload["event"] == "cycle_skipped"
    assert payload["reason"] == "daily_loss_limit"
    assert payload["level"] == "INFO"


def test_structured_formatter_redacts_key_value_secrets_in_messages():
    logger = logging.getLogger("test.secret-message")
    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "login failed password=super-secret MT5_PASSWORD='another-secret'",
        (),
        None,
    )

    payload = json.loads(_StructuredFormatter().format(record))

    assert "super-secret" not in payload["message"]
    assert "another-secret" not in payload["message"]
    assert "[REDACTED]" in payload["message"]


def test_structured_formatter_redacts_secret_values_in_exception_text():
    logger = logging.getLogger("test.secret-exception")
    try:
        raise RuntimeError("connection failed token=exception-secret")
    except RuntimeError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            1,
            "operation failed",
            (),
            __import__("sys").exc_info(),
        )

    payload = json.loads(_StructuredFormatter().format(record))

    assert "exception-secret" not in payload["exception"]
    assert "[REDACTED]" in payload["exception"]
