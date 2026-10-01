from src.python.api import auth


def test_order_review_role_is_exposed_and_configured_for_static_keys():
    service = auth.AuthenticationService(
        environment={
            "API_AUTH_ORDER_REVIEW_TOKEN": "order-review-key",
        }
    )

    principal = service.authenticate(
        api_key="order-review-key",
        authorization=None,
    )

    assert auth.ORDER_REVIEW == "order_review"
    assert principal is not None
    assert principal.roles == frozenset({auth.ORDER_REVIEW})
    assert principal.has(auth.ORDER_REVIEW)
