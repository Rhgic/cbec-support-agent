"""PII 脱敏单测（规格 4.1）。

覆盖：结构化字段识别、同值同占位符、还原精确、加密往返 + 篡改检测。
store_mapping / load_mapping 需 DB，另见集成说明（§15）；这里测纯逻辑与加密层。
"""
import pytest

from app.services import pii
from app.services.pii import mask_text, restore_text


def test_masks_email_phone_card_ip():
    text = "联系 a@b.com 或 +1 415 555 2671，卡 4111 1111 1111 1111，IP 8.8.8.8"
    masked, ph = mask_text(text)
    assert "a@b.com" not in masked
    assert "+1 415 555 2671" not in masked
    assert "4111111111111111" not in masked
    assert "8.8.8.8" not in masked
    # 占位符数量与字段类型齐全（占位符形如 [EMAIL_1]，故带前导方括号）
    assert any(k.startswith("[EMAIL") for k in ph.values())
    assert any(k.startswith("[PHONE") for k in ph.values())
    assert any(k.startswith("[CARD") for k in ph.values())
    assert any(k.startswith("[IP") for k in ph.values())


def test_tracking_keeps_keyword_prefix():
    # 运单号/订单号：保留关键词，只脱敏编号部分
    text = "运单号 SF1234567890 已发出"
    masked, ph = mask_text(text)
    assert "运单号" in masked
    assert "SF1234567890" not in masked
    assert any(k.startswith("[TRACKING") for k in ph.values())


def test_same_value_same_placeholder():
    text = "a@b.com 和 a@b.com 两个邮箱相同"
    masked, ph = mask_text(text)
    # 同一个邮箱只生成一个占位符，且文中两处复用同一占位符
    emails = [v for v in ph.values() if v == "[EMAIL_1]"]
    assert len(emails) == 1
    assert masked.count("[EMAIL_1]") == 2


def test_restore_is_exact():
    text = "订单号 order_ABC123 的物流，邮箱 x@y.com"
    masked, ph = mask_text(text)
    assert restore_text(masked, ph) == text


def test_encrypt_decrypt_roundtrip():
    tok = pii._encrypt("secret-value")
    assert pii._decrypt(tok) == "secret-value"


def test_decrypt_detects_tamper():
    tok = pii._encrypt("secret-value")
    # 改一个 base64 字符，HMAC 校验应失败
    bad = tok[:-1] + ("A" if tok[-1] != "A" else "B")
    with pytest.raises(ValueError):
        pii._decrypt(bad)
