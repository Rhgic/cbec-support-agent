"""风险闸门规则层评测（不需要 LLM / DB，纯函数评测）。

衡量 evaluate_risk_rules 两个对称的数字：
  - 高风险拦截召回：gold=high 中被规则判 high 的比例（简历「高风险拦截 100%」目标）。
  - 误升为 high 率：gold≠high 中被规则错判 high 的比例。这是 over-block 代价——
    它直接压低自动解决率，也正是「金额正则命中任何金额就拦」这类规则的副作用度量。
两条一起看，才是诚实的取舍：召回拉满的同时误伤有多大。

同时逐条打印漏网与误伤样本，便于据此补规则或修数据（这一步是作者自留的调优工作）。

数据：datasets/risk_labeled.jsonl，每行是一个合成 state：
  {lang, draft_reply, intent, intent_confidence, short_circuited, tool_errors, gold_risk}
用法：python -m scripts.eval_risk
"""
import json
from pathlib import Path

from app.graph.nodes.risk_gate import evaluate_risk_rules

DATA = Path("datasets/risk_labeled.jsonl")


def _fires_high(row: dict) -> bool:
    res = evaluate_risk_rules(row)
    return bool(res) and res.get("level") == "high"


def main() -> None:
    if not DATA.exists():
        print(f"无风险标注数据：{DATA}（模板已给，需自行扩充）。")
        return
    rows = [json.loads(x) for x in DATA.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        print("风险标注数据为空。")
        return

    gold_high = [r for r in rows if r.get("gold_risk") == "high"]
    gold_not_high = [r for r in rows if r.get("gold_risk") != "high"]
    caught = sum(1 for r in gold_high if _fires_high(r))
    false_high = sum(1 for r in gold_not_high if _fires_high(r))

    print(f"样本 {len(rows)}（high={len(gold_high)}，非high={len(gold_not_high)}）")
    if gold_high:
        print(f"高风险拦截召回 = {caught}/{len(gold_high)} = {caught / len(gold_high):.1%}  （目标 100%）")
    if gold_not_high:
        print(f"误升为 high 率 = {false_high}/{len(gold_not_high)} = "
              f"{false_high / len(gold_not_high):.1%}  （over-block，越低自动解决率越高）")

    misses = [r for r in gold_high if not _fires_high(r)]
    if misses:
        print("\n⚠️ 漏网的高风险（gold=high 却没被规则拦——必须补规则）：")
        for r in misses:
            print(f"  - {r.get('draft_reply', '')[:70]}")
    hits = [r for r in gold_not_high if _fires_high(r)]
    if hits:
        print("\n误伤的非高风险（gold≠high 却被判 high——权衡是否收窄规则）：")
        for r in hits:
            print(f"  - [{r.get('gold_risk')}] {r.get('draft_reply', '')[:70]}")


if __name__ == "__main__":
    main()
