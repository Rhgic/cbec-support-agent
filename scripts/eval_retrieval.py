"""跨语种检索召回率评测。

为什么按语种分组：BGE-m3 在印尼语(id)上的检索效果预期低于英语(en)，这是预期结果，
须如实按语种分别上报，不得掩盖（规格 1.3 / 15）。

用法：
  1) 先 build_knowledge + embed_knowledge 入库
  2) 准备 datasets/retrieval_eval.jsonl，每行：
     {"lang": "en|es|id", "query": "...", "category": "logistics|return|product",
      "expected_source": "file://<原始md文件名>"}
     用 source_url 而非 chunk_id 作命中判据——chunk_id 每次重建知识库都会变，
     source_url（file://文件名）跨重建稳定。
  3) python -m scripts.eval_retrieval --k 5
输出各语种 recall@k。
"""
import argparse
import json
from collections import defaultdict

from app.database import SessionLocal
from app.services.vectorstore import search


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--data", default="datasets/retrieval_eval.jsonl")
    args = ap.parse_args()

    db = SessionLocal()
    # lang -> [hit, total]
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    try:
        with open(args.data, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                res = search(db, row["category"], row["query"], k=args.k)
                srcs = {r["source_url"] for r in res}
                by_lang[row["lang"]][1] += 1
                if row["expected_source"] in srcs:
                    by_lang[row["lang"]][0] += 1
    finally:
        db.close()

    print(f"retrieval recall@{args.k} (分组按语种):")
    for lang in sorted(by_lang):
        hit, total = by_lang[lang]
        rate = hit / total if total else 0.0
        print(f"  {lang}: {hit}/{total} = {rate:.2%}")


if __name__ == "__main__":
    main()
