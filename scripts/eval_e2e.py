"""端到端批量评测：把标注工单灌进整条 LangGraph，量 auto_solve_rate / grounded_rate。

为什么需要这个脚本（而不是手工跑几条看）：
`eval_rules` / `eval_risk` / `eval_retrieval` 都是**单节点纯函数**评测，测不到「整条链路
跑完到底放行没放行」。而 auto_solve_rate 是整个系统的核心指标，此前的 50%（6/12）是手工
跑出来的一次性数字，改动前后无法复现比对——等于没有尺子就在调优。

本脚本的关键输出不是那一个百分比，而是 **拦截原因直方图**：每条没被自动放行的工单，
是被哪条规则拦下的。只有这个分布才能回答「改动到底动了什么」，
以及「自动解决率涨了，是真的变好，还是把该拦的也放了」。

用法：
    python -m scripts.eval_e2e                      # 全量（约 100 条，真调 LLM）
    python -m scripts.eval_e2e --limit 4            # 每语种每意图各 4 条，快速回归
    python -m scripts.eval_e2e --out runs/before.json
    python -m scripts.eval_e2e --compare runs/before.json --out runs/after.json

前置：db 起着（docker compose up -d db）、Redis 可用、.env 里 DEEPSEEK_API_KEY 已填。
⚠️ Milvus Lite 是单进程嵌入式库——**跑本脚本前必须先停掉 arq worker**，否则开不了第二个
连接（生产切 Milvus standalone 即消除，见 CBEC_MILVUS_URI 的可切换设计）。
"""
import os

# 必须在 torch / pymilvus 被导入前设置，理由同 tasks/worker.py（fork 后段错误）。
# 本脚本不 fork，但 HF tokenizer 的并行线程在批量跑时同样会拖慢并偶发死锁。
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "false")

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from pathlib import Path  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.state import TicketState  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.services.ticket_outcome import apply_ticket_result  # noqa: E402

DATASETS = {
    "en": Path("datasets/tickets_en.jsonl"),
    "es": Path("datasets/tickets_es.jsonl"),
    "id": Path("datasets/tickets_id.jsonl"),
}

# 拦截原因 → 稳定的桶名。用子串匹配而非等值：reasons 里带变量（置信度数值、错误详情）。
# 桶名对齐 risk_gate.evaluate_risk_rules 的七条规则 + generate 的三种升级路径，
# 便于改动后一眼看出「哪条规则的拦截量变了」。
REASON_BUCKETS: list[tuple[str, str]] = [
    ("intent=other", "rule1_intent_other"),
    ("分类置信度", "rule2_low_confidence"),
    ("检索短路", "rule3_short_circuit"),
    ("工具调用失败", "rule4_tool_error"),
    ("回复涉及金额", "rule5_amount"),
    ("回复含确定性承诺", "rule6_promise"),
    ("回复涉及敏感动作", "rule7_sensitive"),
    ("缺少证据引用", "gen_no_citation"),
    ("疑似编造", "gen_fabricated_citation"),
    ("生成失败", "gen_failed"),
    ("脱敏失败", "mask_failed"),
    ("风险闸门异常", "risk_gate_error"),
    ("上游已强制 high", "upstream_forced_high"),
]


def _bucket(reason: str, risk_level: str | None) -> str:
    """把一条 reason 归到稳定桶名。匹配不上的，按「谁写的」归类而不是塞进 other。

    风险闸门有两层：规则层的 reason 是代码里的固定串（可穷举、可审计）；LLM 层的 reason
    是模型现写的自然语言（每次措辞都不同，永远匹配不上固定串）。
    早期版本把后者一律扔进 other，导致改动前后的差值里混着一坨看不懂的残差——
    而这两类的意义完全不同：规则层拦截量的变化是本次改动的直接后果，
    LLM 层判级的变化则是模型波动。必须分开计数，否则对比表读不出因果。
    """
    for needle, name in REASON_BUCKETS:
        if needle in reason:
            return name
    # 规则层命中必然返回 high；因此非 high 的未匹配 reason 只可能出自 LLM 层
    if risk_level in ("low", "mid"):
        return "llm_layer_judgement"
    return "unmatched_high"


def load_rows(limit_per_group: int | None, langs: list[str]) -> list[dict]:
    """按 (lang, gold_intent) 分组取前 N 条——保证抽样在意图上均衡。

    不做随机抽样：评测要可复现，随机会让「改动前后比数字」失去意义。
    """
    rows: list[dict] = []
    for lang in langs:
        path = DATASETS[lang]
        if not path.exists():
            print(f"⚠️ 缺少数据集 {path}，跳过 {lang}")
            continue
        by_intent: dict[str, list[dict]] = defaultdict(list)
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                by_intent[r.get("gold_intent", "other")].append(r)
        for intent in sorted(by_intent):
            group = by_intent[intent]
            rows.extend(group[:limit_per_group] if limit_per_group else group)
    return rows


def run_one(graph, row: dict, eval_run_id: str) -> dict:
    """建真工单 → 跑整图 → 抽出评测关心的字段。

    走真 DB 而非造假 state：mask 节点要写 pii_vault、tools 节点要按 ticket_id 取回真实单号、
    _traced 要写 agent_runs。绕过这些等于评测的不是线上那条链路。
    """
    db = SessionLocal()
    try:
        ticket = Ticket(
            raw_text=row["text"],
            status="processing",
            eval_run_id=eval_run_id,
        )
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id
    finally:
        db.close()

    state: TicketState = {"ticket_id": ticket_id, "raw_text": row["text"]}
    t0 = time.perf_counter()
    try:
        result = graph.invoke(state)
        err = None
    except Exception as e:  # noqa: BLE001 — 单条失败不能中断整批评测
        err = str(e)[:300]
        result = {"fatal_error": err}
    latency_ms = int((time.perf_counter() - t0) * 1000)

    # 与 worker 共用同一个持久化函数，确保 metrics 与离线报告口径一致。
    db = SessionLocal()
    try:
        apply_ticket_result(db, ticket_id, result)
        db.commit()
    finally:
        db.close()

    reasons = result.get("risk_reasons") or []
    return {
        "ticket_id": ticket_id,
        "text": row["text"],
        "gold_lang": row.get("gold_lang"),
        "gold_intent": row.get("gold_intent"),
        "lang": result.get("lang"),
        "intent": result.get("intent"),
        "intent_method": result.get("intent_method"),
        "intent_confidence": result.get("intent_confidence"),
        "retrieval_score": result.get("retrieval_score"),
        "short_circuited": bool(result.get("short_circuited")),
        "tool_errors": result.get("tool_errors") or [],
        "citations": result.get("citations") or [],
        "risk_level": result.get("risk_level"),
        "action": result.get("action"),
        "risk_reasons": reasons,
        "reason_buckets": sorted({_bucket(r, result.get("risk_level")) for r in reasons}),
        "reached_generate": bool(result.get("draft_reply")),
        "latency_ms": latency_ms,
        "error": err,
    }


def summarize(records: list[dict]) -> dict:
    n = len(records)
    auto = [r for r in records if r["action"] == "auto_send"]

    # grounded_rate 的分母是「真正走到生成的工单」：短路/早退的工单压根没检索结果可引用，
    # 把它们算进分母会把一个「生成质量」指标污染成「检索覆盖」指标。两个数都报，不藏。
    generated = [r for r in records if r["reached_generate"]]
    grounded = [r for r in generated if r["citations"]]

    by_intent: dict[str, dict] = {}
    for r in records:
        k = r["gold_intent"] or "unknown"
        b = by_intent.setdefault(k, {"n": 0, "auto": 0})
        b["n"] += 1
        b["auto"] += 1 if r["action"] == "auto_send" else 0

    by_lang: dict[str, dict] = {}
    for r in records:
        k = r["gold_lang"] or "unknown"
        b = by_lang.setdefault(k, {"n": 0, "auto": 0})
        b["n"] += 1
        b["auto"] += 1 if r["action"] == "auto_send" else 0

    buckets: Counter = Counter()
    for r in records:
        for b in r["reason_buckets"]:
            buckets[b] += 1

    return {
        "n": n,
        "auto_solve_rate": len(auto) / n if n else 0.0,
        "auto_send": len(auto),
        "grounded_rate": len(grounded) / len(generated) if generated else 0.0,
        "grounded": len(grounded),
        "reached_generate": len(generated),
        "short_circuit_rate": sum(r["short_circuited"] for r in records) / n if n else 0.0,
        "errors": sum(1 for r in records if r["error"]),
        "by_intent": by_intent,
        "by_lang": by_lang,
        "block_reasons": dict(buckets.most_common()),
        "p50_latency_ms": sorted(r["latency_ms"] for r in records)[n // 2] if n else 0,
    }


def _pct(x: float) -> str:
    return f"{x:.1%}"


def print_report(s: dict) -> None:
    print("\n" + "=" * 62)
    print(f"端到端批量评测  n={s['n']}  （错误 {s['errors']} 条）")
    print("=" * 62)
    if s["errors"]:
        # 基建挂了（Milvus 锁、DB、LLM key）时下面每个百分比都是假的——必须显式说破，
        # 否则「auto_solve_rate 0%」会被当成模型效果，而不是环境没起来。
        print(f"⚠️ 有 {s['errors']}/{s['n']} 条抛异常，以下指标不可信；"
              f"先排查环境（worker 是否占着 Milvus Lite 锁、db/Redis/LLM key）。")
    print(f"auto_solve_rate    = {s['auto_send']}/{s['n']} = {_pct(s['auto_solve_rate'])}   目标 ≥70%")
    print(f"grounded_rate      = {s['grounded']}/{s['reached_generate']} = "
          f"{_pct(s['grounded_rate'])}   （分母＝走到生成的工单）目标 ≥95%")
    print(f"短路率             = {_pct(s['short_circuit_rate'])}")
    print(f"p50 端到端时延     = {s['p50_latency_ms']} ms")

    print("\n按意图（自动解决率）")
    for k in sorted(s["by_intent"]):
        b = s["by_intent"][k]
        print(f"  {k:<10} {b['auto']}/{b['n']} = {_pct(b['auto'] / b['n']) if b['n'] else '-'}")

    print("\n按语种（自动解决率）")
    for k in sorted(s["by_lang"]):
        b = s["by_lang"][k]
        print(f"  {k:<10} {b['auto']}/{b['n']} = {_pct(b['auto'] / b['n']) if b['n'] else '-'}")

    print("\n拦截原因分布（一条工单可命中多条；这是看懂改动的关键）")
    if not s["block_reasons"]:
        print("  （无拦截）")
    for name, cnt in s["block_reasons"].items():
        bar = "█" * min(40, cnt)
        print(f"  {name:<26} {cnt:>3}  {bar}")


def print_diff(base: dict, cur: dict) -> None:
    print("\n" + "=" * 62)
    print("对比基线")
    print("=" * 62)

    def row(label: str, a: float, b: float) -> None:
        delta = b - a
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "＝")
        print(f"  {label:<20} {_pct(a):>7} → {_pct(b):>7}   {arrow} {delta:+.1%}")

    row("auto_solve_rate", base["auto_solve_rate"], cur["auto_solve_rate"])
    row("grounded_rate", base["grounded_rate"], cur["grounded_rate"])
    row("短路率", base["short_circuit_rate"], cur["short_circuit_rate"])

    print("\n  拦截原因增减（负数＝拦得少了，需逐条确认是「不该拦」而非「漏拦」）")
    keys = set(base["block_reasons"]) | set(cur["block_reasons"])
    for k in sorted(keys, key=lambda x: -abs(cur["block_reasons"].get(x, 0)
                                             - base["block_reasons"].get(x, 0))):
        a = base["block_reasons"].get(k, 0)
        b = cur["block_reasons"].get(k, 0)
        if a != b:
            print(f"    {k:<26} {a:>3} → {b:>3}  ({b - a:+d})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="每（语种×意图）取前 N 条；不传＝全量")
    ap.add_argument("--langs", default="en,es,id")
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径")
    ap.add_argument("--compare", default=None, help="与该基线 JSON 对比")
    args = ap.parse_args()

    langs = [x.strip() for x in args.langs.split(",") if x.strip() in DATASETS]
    rows = load_rows(args.limit, langs)
    if not rows:
        print("没有可评测的工单。")
        return

    print(f"准备评测 {len(rows)} 条工单（真调 LLM，请确认 worker 已停）…")
    eval_run_id = str(uuid.uuid4())
    print(f"评测批次 eval_run_id={eval_run_id}")
    graph = build_graph().compile()

    records = []
    for i, row in enumerate(rows, 1):
        rec = run_one(graph, row, eval_run_id)
        records.append(rec)
        flag = "✅" if rec["action"] == "auto_send" else "🔸"
        if rec["error"]:
            flag = "❌"
        print(f"  [{i:>3}/{len(rows)}] {flag} {rec['gold_lang']}/{rec['gold_intent']:<9} "
              f"→ {str(rec['action']):<14} {rec['text'][:44]}")

    summary = summarize(records)
    summary["eval_run_id"] = eval_run_id
    print_report(summary)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"summary": summary, "records": records},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n已写入 {out}")

    if args.compare:
        base_path = Path(args.compare)
        if base_path.exists():
            base = json.loads(base_path.read_text(encoding="utf-8"))["summary"]
            print_diff(base, summary)
        else:
            print(f"\n⚠️ 基线文件不存在：{base_path}")


if __name__ == "__main__":
    main()
