from src.python.execution.scoring import ScoringSystem


def test_dashboard_score_treats_missing_confidence_as_zero():
    score = ScoringSystem().calculate_score(
        {"symbol": "EURUSD", "direction": "HOLD", "confidence": None}
    )

    assert score >= 0.0
