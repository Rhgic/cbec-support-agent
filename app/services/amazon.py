"""Amazon SP-API order inspection and permitted message discovery.

Fixed Amazon hosts, no PII data sets, no outbound buyer-message operations.
"""

import re
import threading
import time
from functools import lru_cache

import httpx

from app.config import get_settings

ORDER_ID = re.compile(r"\d{3}-\d{7}-\d{7}", re.ASCII)


class AmazonError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class AmazonClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        host = f"{'sandbox.' if settings.amazon_sandbox else ''}sellingpartnerapi-{settings.amazon_region}.amazon.com"
        self.base = f"https://{host}"
        self.http = httpx.Client(transport=transport, timeout=15, follow_redirects=False)
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def readiness(self):
        missing = [
            name.upper()
            for name in ("amazon_marketplace_id", "amazon_client_id", "amazon_client_secret", "amazon_refresh_token")
            if not self._setting(name)
        ]
        return {
            "enabled": self.settings.amazon_enabled,
            "configured": not missing,
            "missing": missing,
            "sandbox": self.settings.amazon_sandbox,
            "region": self.settings.amazon_region,
            "capabilities": ["order_summary", "available_message_types"],
            "buyer_message_sending": False,
        }

    def _setting(self, name):
        value = getattr(self.settings, name)
        return value.get_secret_value() if hasattr(value, "get_secret_value") else value

    def _access_token(self):
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            try:
                response = self.http.post(
                    "https://api.amazon.com/auth/o2/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._setting("amazon_refresh_token"),
                        "client_id": self._setting("amazon_client_id"),
                        "client_secret": self._setting("amazon_client_secret"),
                    },
                )
                response.raise_for_status()
                data = response.json()
                token = data["access_token"]
                expiry = int(data["expires_in"])
                if not isinstance(token, str) or not token or expiry <= 0:
                    raise ValueError("invalid access token")
                self._token = token
                self._expires = time.monotonic() + max(0, expiry - 60)
                return token
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                # Never include upstream bodies or credentials in public errors.
                raise AmazonError(502, "Amazon 授权失败，请核对卖家应用凭据及授权") from None

    def _get(self, path, params=None):
        state = self.readiness()
        if not state["enabled"] or not state["configured"]:
            raise AmazonError(503, "Amazon 接入尚未启用或配置不完整")
        try:
            response = self.http.get(
                self.base + path,
                params=params,
                headers={
                    "x-amz-access-token": self._access_token(),
                    "User-Agent": "CBEC-Support/2.0 (Language=Python)",
                },
            )
            if response.status_code == 401:
                with self._lock:
                    self._token = ""
                raise AmazonError(502, "Amazon 授权已失效，请重试或重新授权")
            if response.status_code == 429:
                raise AmazonError(429, "Amazon 接口限流，请稍后重试")
            if response.status_code in (403, 404):
                raise AmazonError(response.status_code, "Amazon 订单不可访问或应用权限不足")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid response")
            return data
        except (httpx.HTTPError, ValueError):
            raise AmazonError(502, "Amazon 服务异常，请稍后重试") from None

    def order_summary(self, order_id):
        if not ORDER_ID.fullmatch(order_id):
            raise AmazonError(422, "Amazon 订单号应为 3-7-7 位数字格式")
        data = self._get(f"/orders/2026-01-01/orders/{order_id}", {"includedData": "FULFILLMENT"})
        order = data.get("order")
        if not isinstance(order, dict) or order.get("orderId") != order_id:
            raise AmazonError(502, "Amazon 返回无效的订单数据")
        sales = order.get("salesChannel") or {}
        if not isinstance(sales, dict) or sales.get("marketplaceId") != self.settings.amazon_marketplace_id:
            raise AmazonError(403, "订单与配置的 Amazon 站点不匹配")
        fulfillment = order.get("fulfillment") or {}
        if not isinstance(fulfillment, dict):
            raise AmazonError(502, "Amazon 返回无效的履约状态")
        for value in (
            order.get("createdTime"),
            order.get("lastUpdatedTime"),
            fulfillment.get("fulfillmentStatus"),
            fulfillment.get("fulfilledBy"),
        ):
            if value is not None and not isinstance(value, str):
                raise AmazonError(502, "Amazon 返回无效的订单字段")
        return {
            "order_id": order_id,
            "marketplace_id": sales["marketplaceId"],
            "created_at": order.get("createdTime"),
            "updated_at": order.get("lastUpdatedTime"),
            "fulfillment_status": fulfillment.get("fulfillmentStatus"),
            "fulfilled_by": fulfillment.get("fulfilledBy"),
            "sandbox": self.settings.amazon_sandbox,
        }

    def message_actions(self, order_id):
        self.order_summary(order_id)  # also checks marketplace and order access
        data = self._get(f"/messaging/v1/orders/{order_id}", {"marketplaceIds": self.settings.amazon_marketplace_id})
        links = data.get("_links") or {}
        if not isinstance(links, dict):
            raise AmazonError(502, "Amazon 返回无效的消息类型")
        actions = links.get("actions", [])
        if not isinstance(actions, list):
            raise AmazonError(502, "Amazon 返回无效的消息类型")
        # Never follow upstream href URLs or treat an available action as an arbitrary reply API.
        names = sorted(
            {item["name"] for item in actions if isinstance(item, dict) and isinstance(item.get("name"), str)}
        )
        return {"order_id": order_id, "available_message_types": names, "buyer_message_sending": False}


@lru_cache
def get_amazon_client():
    return AmazonClient(get_settings())
