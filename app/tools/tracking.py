"""17TRACK 物流轨迹客户端（规格 7.1）。

两步：register(tracking_no, carrier) → get_track_info(tracking_no)。
额度模型：register 扣 1 额度；注册后重复查询不扣。因此 register 必须本地去重，
已注册/已缓存的单号不得重复注册（tracking_cache 主键即去重依据 + 进程内集合）。
缓存层：所有返回落库 tracking_cache；TRACKING_MODE=cache 时只读缓存，不调真实 API、
不扣额度，可反复演示时序轨迹。

detect_exception 是业务逻辑（不是 API 转发）：超时未更新 / 清关滞留 / 目的地退回。
"""
import json
from datetime import UTC, datetime

from app.config import get_settings
from app.database import SessionLocal
from app.models import TrackingCache

settings = get_settings()

# 进程内去重：本次运行已注册过的单号不再打 register 接口
_registered: set[str] = set()

API_BASE = "https://api.17track.net/track/v3"


def _utc_parse(s: str | None) -> datetime | None:
    if not s:
        return None
    # 17TRACK 时间多为 ISO8601 含 Z；解析失败返回 None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _normalize(raw: dict) -> dict:
    """把 17TRACK v3 响应规范化为本系统的轨迹结构。"""
    data = raw.get("data", {})
    # gettrackinfo 返回 data.tracking.events；register 无轨迹
    tracking = data.get("tracking", {}) if isinstance(data, dict) else {}
    events_raw = tracking.get("events", []) if isinstance(tracking, dict) else []
    events = []
    for e in events_raw:
        events.append(
            {
                "status": str(e.get("status") or e.get("description") or ""),
                "time": e.get("date"),
                "location": e.get("location") or "",
            }
        )
    delivered = any("delivered" in e["status"].lower() or "签收" in e["status"] for e in events)
    return {
        "tracking_no": data.get("number") or tracking.get("number"),
        "delivered": delivered,
        "events": events,
    }


def register(tracking_no: str, carrier: str) -> bool:
    """注册运单以开启追踪。已注册/已缓存则跳过（额度保护）。返回是否真正发起注册。"""
    if tracking_no in _registered:
        return False
    # 本地缓存已有该单号 → 视为已注册，不再打接口
    db = SessionLocal()
    try:
        if db.get(TrackingCache, tracking_no) is not None:
            _registered.add(tracking_no)
            return False
    finally:
        db.close()

    if settings.tracking_mode != "live" or not settings.tracking_api_key:
        # 非 live 模式不真正注册，仅标记进程内已处理
        _registered.add(tracking_no)
        return False

    import httpx  # 延迟导入

    try:
        resp = httpx.post(
            f"{API_BASE}/register",
            headers={"17token": settings.tracking_api_key, "Content-Type": "application/json"},
            json=[{"number": tracking_no, "carrier": carrier}],
            timeout=5,
        )
        resp.raise_for_status()
        _registered.add(tracking_no)
        return True
    except Exception:
        # 注册失败不阻断：后续 get_track_info 仍可能命中缓存
        return False


def get_track_info(tracking_no: str) -> dict | None:
    """返回规范化的轨迹结构；缓存命中直接返回，live 模式调用并落库。"""
    db = SessionLocal()
    try:
        row = db.get(TrackingCache, tracking_no)
        if row is not None:
            return json.loads(row.raw_json)
    finally:
        db.close()

    if settings.tracking_mode != "live" or not settings.tracking_api_key:
        return None  # 缓存未命中且非 live：无数据

    import httpx

    resp = httpx.post(
        f"{API_BASE}/gettrackinfo",
        headers={"17token": settings.tracking_api_key, "Content-Type": "application/json"},
        json=[{"number": tracking_no}],
        timeout=5,
    )
    resp.raise_for_status()
    payload = resp.json()
    normalized = _normalize(payload)
    db = SessionLocal()
    try:
        db.merge(TrackingCache(tracking_no=tracking_no, raw_json=json.dumps(normalized, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    return normalized


def detect_exception(
    track_info: dict | None,
    no_update_days: int = 7,
    customs_days: int = 14,
) -> Exception | None:
    """业务逻辑：判断轨迹是否异常。无异常返回 None。

    - 超时未更新：距最后一次轨迹更新 > no_update_days 天且未签收
    - 清关滞留：停留在清关状态 > customs_days 天
    - 目的地退回：出现退回类状态
    """
    if not track_info:
        return RuntimeError("无轨迹数据")
    events = track_info.get("events", [])
    if not events:
        return RuntimeError("轨迹为空，无法判定")

    # 假设 events 按时间倒序，第 0 条为最新
    last_time = _utc_parse(events[0].get("time"))
    delivered = track_info.get("delivered", False)
    now = datetime.now(UTC)

    if not delivered and last_time is not None and (now - last_time).days > no_update_days:
        return TimeoutError(f"距最后轨迹更新已超过 {no_update_days} 天且未签收")

    for e in events:
        st = e.get("status", "").lower()
        if "customs" in st or "清关" in st:
            ct = _utc_parse(e.get("time"))
            if ct is not None and (now - ct).days > customs_days:
                return RuntimeError(f"停留在清关状态已超过 {customs_days} 天")
            break

    for e in events:
        st = e.get("status", "").lower()
        if "return" in st or "退回" in st:
            return RuntimeError("包裹目的地退回")

    return None
