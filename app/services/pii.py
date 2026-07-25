"""PII 识别与还原：仅结构化字段。

为什么只做结构化（邮箱/电话/信用卡/运单号/订单号/IP）：人名、地址的正则对西语/印尼语
召回极低，属已知短板（规格 15.1），绝不声称已覆盖。同值同占位符，映射加密后落
pii_vault，仅在出站前一步还原——LLM 全程接触不到原文。

加密说明（重要）：依赖清单（规格 2.2）不含 cryptography，故这里用 stdlib 派生的密钥
做「XOR + HMAC」可逆变换，属于轻量混淆而非 AES 级加密。生产环境应改用 cryptography
的 Fernet/AES-GCM，并补充该依赖。
"""
import base64
import hashlib
import hmac
import re

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import PiiVault

settings = get_settings()

# 仅结构化 PII；人名/地址不在此列（已知短板）
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 电话：要求前后都不是字母数字（避免吃进运单号/订单号里的数字串），
# 并允许数字之间用空格/短横分隔（如 +1 415 555 2671），共 7-14 位数字
PHONE = re.compile(r"(?<![A-Za-z0-9])(?:\+\d{1,3}[\s-]?)?\d(?:[\s-]?\d){6,13}(?![A-Za-z0-9])")
CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 运单号/订单号：关键词锚定，仅脱敏编号部分，保留关键词前缀
# num 允许下划线（真实订单号常含 order_ABC123），避免还原后缺失前缀
# sep 捕获关键词与编号之间的分隔（如空格），还原时补回，保证脱敏文本可精确还原
TRACKING = re.compile(
    r"(?P<kw>运单号|订单号|单号|tracking(?:\s*number)?|order(?:\s*number)?|no\.?)"
    r"(?P<sep>[^\n\r]{0,8}?)"
    r"(?P<num>[A-Za-z0-9_]{6,22})"
)


def _key() -> bytes:
    return hashlib.sha256(settings.pii_secret.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt(plain: str) -> str:
    body = _xor(plain.encode("utf-8"), _key())
    sig = hmac.new(_key(), body, hashlib.sha256).digest()
    return base64.b64encode(sig + body).decode("ascii")


def _decrypt(token: str) -> str:
    blob = base64.b64decode(token)
    sig, body = blob[:32], blob[32:]
    expected = hmac.new(_key(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("PII 完整性校验失败，可能被篡改")
    return _xor(body, _key()).decode("utf-8")


def mask_text(text: str) -> tuple[str, dict[str, str]]:
    """返回 (脱敏后文本, {原文: 占位符})。同值复用同一占位符。"""
    placeholders: dict[str, str] = {}
    counters: dict[str, int] = {}

    def next_ph(kind: str, val: str) -> str:
        if val in placeholders:  # 同值同占位符，避免还原时多对一错乱
            return placeholders[val]
        counters[kind] = counters.get(kind, 0) + 1
        ph = f"[{kind}_{counters[kind]}]"
        placeholders[val] = ph
        return ph

    masked = text
    # 顺序：CARD 先于 PHONE。卡号 13-16 位、电话 ≤14 位，先跑卡号可把长数字串整段认领，
    # 避免电话正则抢走卡号前 14 位导致卡号被截断脱敏。
    for kind, pat in (("EMAIL", EMAIL), ("CARD", CARD), ("PHONE", PHONE), ("IP", IP)):
        masked = pat.sub(lambda m, k=kind: next_ph(k, m.group(0)), masked)

    def _track_sub(m: re.Match) -> str:
        # 还原时补回 kw 与 num 之间的分隔（如空格），保证精确还原
        return m.group("kw") + m.group("sep") + next_ph("TRACKING", m.group("num"))

    masked = TRACKING.sub(_track_sub, masked)
    return masked, placeholders


def restore_text(masked: str, placeholders: dict[str, str]) -> str:
    """把占位符还原为原文（出站前一步调用）。"""
    reverse = {ph: val for val, ph in placeholders.items()}
    out = masked
    for ph, val in reverse.items():
        out = out.replace(ph, val)
    return out


def store_mapping(ticket_id: int, placeholders: dict[str, str]) -> None:
    """把 {原文: 占位符} 加密写入 pii_vault。"""
    db = SessionLocal()
    try:
        for original, placeholder in placeholders.items():
            db.add(
                PiiVault(
                    ticket_id=ticket_id,
                    placeholder=placeholder,
                    original=_encrypt(original),
                )
            )
        db.commit()
    finally:
        db.close()


def load_mapping(ticket_id: int) -> dict[str, str]:
    """读取并返回 {占位符: 原文}，供出站前还原。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(PiiVault.placeholder, PiiVault.original).where(
                PiiVault.ticket_id == ticket_id
            )
        ).fetchall()
        return {ph: _decrypt(enc) for ph, enc in rows}
    finally:
        db.close()
