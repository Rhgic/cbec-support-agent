"""埋点指标单测（规格 10）。

验证 record_ticket_outcome 正确累加 tickets_total / cost_total_usd，
且按 (lang,intent,action,risk_level) 四维打标签。
注：prometheus_client 的 get_sample_value 对「无标签」Counter 在本版本返回 None，
故读取无标签成本指标时走 REGISTRY.collect() 取样本值（/metrics 端点本身用
generate_latest 不受影响）。
"""
from app.metrics import events
from app.observability import REGISTRY


def _read_counter(name: str, labels: dict | None = None) -> float:
    # prometheus_client 的 Counter：若指标名不以 _total 结尾（如 cost_total_usd），
    # 样本名会自动补成 cost_total_usd_total；若已结尾（如 tickets_total）则样本名不变。
    # 两种样本名都匹配，避免漏读。
    targets = {name, name + "_total"}
    for m in REGISTRY.collect():
        for s in m.samples:
            if s.name in targets and (labels is None or s.labels == labels):
                return float(s.value)
    return 0.0


def test_record_ticket_outcome_increments():
    labels = dict(lang="en", intent="logistics", action="auto_send", risk_level="low")
    before = _read_counter("tickets_total", labels)
    cost_before = _read_counter("cost_total_usd")

    events.record_ticket_outcome(
        lang="en", intent="logistics", action="auto_send", risk_level="low", cost_usd=0.002, tokens=10
    )

    after = _read_counter("tickets_total", labels)
    cost_after = _read_counter("cost_total_usd")
    assert after - before == 1
    assert cost_after - cost_before == 0.002


def test_distinct_labels_separate_series():
    en_labels = dict(lang="en", intent="logistics", action="auto_send", risk_level="low")
    before = _read_counter("tickets_total", en_labels)
    # 记录一条完全不同标签的工单，不应污染 en/logistics 的 series
    events.record_ticket_outcome(lang="es", intent="return", action="human_required", risk_level="high")
    after = _read_counter("tickets_total", en_labels)
    assert after - before == 0
