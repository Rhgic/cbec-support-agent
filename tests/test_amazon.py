"""Amazon transport contract tests: no real credentials or API calls."""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.routers.amazon import router
from app.services.amazon import AmazonClient, AmazonError

ORDER = "123-1234567-1234567"


def settings(**values):
    return Settings(
        _env_file=None,
        amazon_enabled=True,
        amazon_marketplace_id="MARKET",
        amazon_client_id="client",
        amazon_client_secret="secret",
        amazon_refresh_token="refresh",
        **values,
    )


def test_auth_refresh_caching_and_allowlist():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "api.amazon.com":
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        assert request.url.host == "sandbox.sellingpartnerapi-na.amazon.com"
        assert request.headers["x-amz-access-token"] == "access"
        assert request.url.params["includedData"] == "FULFILLMENT"
        return httpx.Response(
            200,
            json={
                "order": {
                    "orderId": ORDER,
                    "salesChannel": {"marketplaceId": "MARKET"},
                    "buyer": {"buyerEmail": "private@example.com"},
                    "recipient": {"name": "Private"},
                    "fulfillment": {"fulfillmentStatus": "SHIPPED", "fulfilledBy": "AMAZON"},
                }
            },
        )

    client = AmazonClient(settings(), transport=httpx.MockTransport(handle))
    result = client.order_summary(ORDER)
    assert result["fulfillment_status"] == "SHIPPED"
    assert "private@example.com" not in str(result) and "buyer" not in result
    client.order_summary(ORDER)
    assert len([r for r in requests if r.url.host == "api.amazon.com"]) == 1


@pytest.mark.parametrize("order", ["../../bad", "123", "１２３-1234567-1234567"])
def test_invalid_order_never_calls_amazon(order):
    client = AmazonClient(settings(), transport=httpx.MockTransport(lambda req: pytest.fail("network")))
    with pytest.raises(AmazonError) as error:
        client.order_summary(order)
    assert error.value.status == 422


def test_disabled_client_never_calls_amazon():
    client = AmazonClient(Settings(_env_file=None), transport=httpx.MockTransport(lambda req: pytest.fail("network")))
    with pytest.raises(AmazonError) as error:
        client.order_summary(ORDER)
    assert error.value.status == 503


def test_upstream_errors_hide_secrets():
    def handle(request):
        return httpx.Response(403, text="secret token credential")

    client = AmazonClient(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(AmazonError) as error:
        client.order_summary(ORDER)
    assert "credential" not in str(error.value) and "secret" not in str(error.value)


def test_amazon_read_endpoints_require_operator_token():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        for path in ("/status", f"/orders/{ORDER}", f"/orders/{ORDER}/message-actions"):
            assert client.get("/integrations/amazon" + path).status_code == 401


def test_marketplace_mismatch_blocks_actions():
    def handle(request):
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        assert "/messaging/" not in request.url.path
        return httpx.Response(200, json={"order": {"orderId": ORDER, "salesChannel": {"marketplaceId": "OTHER"}}})

    client = AmazonClient(settings(), transport=httpx.MockTransport(handle))
    with pytest.raises(AmazonError) as error:
        client.message_actions(ORDER)
    assert error.value.status == 403


def test_available_actions_are_names_only_and_links_not_followed():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})
        if request.url.path.startswith("/orders/"):
            return httpx.Response(200, json={"order": {"orderId": ORDER, "salesChannel": {"marketplaceId": "MARKET"}}})
        return httpx.Response(
            200,
            json={
                "_links": {"actions": [{"name": "confirmOrderDetails", "href": "https://untrusted.example/message"}]}
            },
        )

    client = AmazonClient(settings(), transport=httpx.MockTransport(handle))
    response = client.message_actions(ORDER)
    assert response["available_message_types"] == ["confirmOrderDetails"]
    assert response["buyer_message_sending"] is False
    assert len(paths) == 3
