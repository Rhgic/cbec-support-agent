"""规则分类器命中率与误判率评测（不需要 LLM / DB，纯函数评测）。

衡量 classify_by_rules 的两个数字：
  - coverage（命中率）：规则层直接出结果的比例 = 省下的 LLM 调用比例
    （简历「规则前置使 XX% 查询免调 LLM」的数字来源）。
  - 误判率：规则命中的样本里 intent 判错的比例。分类器原则是「宁可漏判(交 LLM)
    不可误判(错 intent → 错工具 → 差体验)」——误判率比命中率更该盯。
两者都按语种、按意图分组，便于看清 BGE 之外规则本身在哪个语种/意图上弱。

数据：datasets/tickets_{en,es,id}.jsonl，每行 {lang, text, gold_lang, gold_intent}。
用法：python -m scripts.eval_rules
"""
import json
from collections import defaultdict
from pathlib import Path

from app.graph.nodes.classify import classify_by_rules

FILES = [
    "datasets/tickets_en.jsonl",
    "datasets/tickets_es.jsonl",
    "datasets/tickets_id.jsonl",
]


def _load() -> list[dict]:
    rows: list[dict] = []
    for fp in FILES:
        p = Path(fp)
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _rate(n: int, d: int) -> str:
    return f"{n}/{d} = {(n / d if d else 0):.0%}"


def main() -> None:
    rows = _load()
    if not rows:
        print("无工单数据。请先填充 datasets/tickets_{en,es,id}.jsonl（模板已给，需自行扩充）。")
        return

    total = len(rows)
    fired = intent_wrong = lang_wrong = 0
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])   # gold_lang -> [fired, intent_wrong, total]
    by_intent: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # gold_intent -> [fired, intent_wrong, total]

    for r in rows:
        gl, gi = r.get("gold_lang"), r.get("gold_intent")
        by_lang[gl][2] += 1
        by_intent[gi][2] += 1
        res = classify_by_rules(r["text"])
        if res is None:
            continue  # 规则未命中 → 交 LLM，不计入误判（这是刻意的「漏判优于误判」）
        fired += 1
        by_lang[gl][0] += 1
        by_intent[gi][0] += 1
        if res["intent"] != gi:
            intent_wrong += 1
            by_lang[gl][1] += 1
            by_intent[gi][1] += 1
        if res["lang"] != gl:
            lang_wrong += 1

    print(f"样本 {total} 条")
    print(f"规则命中率 coverage = {_rate(fired, total)}  （= 省下的 LLM 调用比例）")
    if fired:
        print(f"命中样本 intent 误判率 = {_rate(intent_wrong, fired)}")
        print(f"命中样本 lang  误判率 = {_rate(lang_wrong, fired)}")
    print("\n按 gold 语种（命中率 | 命中内误判率）：")
    for k in sorted(by_lang, key=lambda x: str(x)):
        fi, iw, tt = by_lang[k]
        print(f"  {k}: 命中 {_rate(fi, tt)} | 误判 {_rate(iw, fi)}")
    print("按 gold 意图（命中率 | 命中内误判率）：")
    for k in sorted(by_intent, key=lambda x: str(x)):
        fi, iw, tt = by_intent[k]
        print(f"  {k}: 命中 {_rate(fi, tt)} | 误判 {_rate(iw, fi)}")


if __name__ == "__main__":
    main()
