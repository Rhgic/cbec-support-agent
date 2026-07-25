"""检索置信度阈值扫描：产出「误拒率 vs 幻觉暴露率」权衡曲线，供人工选取拐点。

与 eval_retrieval.py 的区别：
  eval_retrieval 衡量 recall@k（检索本身准不准）；
  本脚本衡量「短路阈值 THRESHOLD 定在哪」——需要两类样本：
    - answerable（datasets/retrieval_eval.jsonl）：知识库中确有答案。
      在阈值 t 下 top1 < t 被短路 = 误拒（false reject），越低越好。
    - adversarial（datasets/adversarial.jsonl）：知识库中无答案的离题问题。
      在阈值 t 下 top1 >= t 未被短路 = 幻觉暴露（会进生成节点乱答），越低越好。

对每个 t 同时给出两条曲线，人工取二者都可接受的拐点，替换 retrieve.py 的 THRESHOLD。
读曲线、拍板阈值是作者自留的面试论据——脚本只产出数据，不替你决定阈值。

前置：先 build_knowledge + embed_knowledge 把知识库灌进 pgvector。
用法：
  python -m scripts.sweep_threshold --lo 0.40 --hi 0.80 --step 0.05
"""
import argparse
import json

from app.database import SessionLocal
from app.services.vectorstore import search


def _load(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


def _top1(db, row: dict) -> float:
    # 取召回候选内最大向量相似度——与 retrieve 短路判定一致；rerank 只重排顺序、不改此值，
    # 故用较大 k 拿全部候选再取 max（拿 k=1 会得到 rerank 后的首条，其向量分未必最大）。
    res = search(db, row["category"], row["query"], k=20)
    return max((r["score"] for r in res), default=0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answerable", default="datasets/retrieval_eval.jsonl")
    ap.add_argument("--adversarial", default="datasets/adversarial.jsonl")
    ap.add_argument("--lo", type=float, default=0.40)
    ap.add_argument("--hi", type=float, default=0.80)
    ap.add_argument("--step", type=float, default=0.05)
    args = ap.parse_args()

    ans = _load(args.answerable)
    adv = _load(args.adversarial)
    if not ans or not adv:
        print("需要 answerable 与 adversarial 两个数据集都非空：")
        print(f"  answerable : {len(ans)} 条 ({args.answerable})")
        print(f"  adversarial: {len(adv)} 条 ({args.adversarial})")
        print("adversarial 需自行扩充为足量真实离题问题，样本太少曲线无意义。")
        return

    db = SessionLocal()
    try:
        ans_scores = [_top1(db, r) for r in ans]
        adv_scores = [_top1(db, r) for r in adv]
    finally:
        db.close()

    print(f"answerable={len(ans_scores)}  adversarial={len(adv_scores)}\n")
    print(f"{'阈值':>6} | {'误拒率(有答案却短路)':>20} | {'幻觉暴露(无答案却放行)':>22}")
    print("-" * 58)
    t = args.lo
    while t <= args.hi + 1e-9:
        false_reject = sum(1 for s in ans_scores if s < t) / len(ans_scores)
        hallucination = sum(1 for s in adv_scores if s >= t) / len(adv_scores)
        print(f"{t:>6.2f} | {false_reject:>20.1%} | {hallucination:>22.1%}")
        t += args.step
    print("\n取「误拒率」「幻觉暴露」都可接受的最小 t 作为 THRESHOLD；拐点由你判断并留存本表。")


if __name__ == "__main__":
    main()
