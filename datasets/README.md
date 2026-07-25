# datasets/ 说明

> ⚠️ 本目录下所有 jsonl 都是**结构完整、内容为少量种子**的模板。
> 种子只够让脚本跑通、验证格式；**面试要用的数字必须建立在你自己扩充的足量数据上。**
> 种子样本是合成的——面试若被问「你的测试集怎么设计的」，答案要是「我扩充并核对过的」，
> 不能是「AI 给的 13 条」。每个文件先扩到 ≥50 条、覆盖真实表达后再跑。

## 文件与用途

| 文件 | 消费它的脚本 | 产出的指标 |
|---|---|---|
| `knowledge_raw/<category>/*.md` | `build_knowledge --all` → `embed_knowledge` | 知识库本身 |
| `retrieval_eval.jsonl` | `eval_retrieval.py` | 跨语种 recall@k（分语种） |
| `retrieval_eval.jsonl` + `adversarial.jsonl` | `sweep_threshold.py` | 阈值曲线：误拒率 vs 幻觉暴露 → 定 THRESHOLD |
| `adversarial.jsonl` | `sweep_threshold.py` | 无答案离题问题（应被短路） |
| `tickets_{en,es,id}.jsonl` | `eval_rules.py` | 规则命中率 + 误判率（分语种/意图） |
| `risk_labeled.jsonl` | `eval_risk.py` | 高风险拦截召回 + 误升 high 率 |

## 各文件 schema

- **retrieval_eval.jsonl**：`{lang, query, category, expected_source}`
  `expected_source` = `file://<md文件名>`（用文件名而非 chunk_id，重建知识库不失效）。
- **adversarial.jsonl**：`{lang, query, category}` —— 知识库里**没有**答案的离题问题。
- **tickets_{en,es,id}.jsonl**：`{lang, text, gold_lang, gold_intent}`
  `gold_intent ∈ {logistics, return, product, other}`。
- **risk_labeled.jsonl**：一个合成 state，
  `{lang, draft_reply, intent, intent_confidence, short_circuited, tool_errors, gold_risk}`
  `gold_risk ∈ {low, mid, high}`。

## 跑法（需先起 Postgres+pgvector、下 BGE-m3、灌库）

```bash
# 1. 建库（--all 按子目录逐类，套用正确 category）
python -m scripts.build_knowledge --all
python -m scripts.embed_knowledge

# 2. 检索召回（分语种）
python -m scripts.eval_retrieval --k 5

# 3. 阈值曲线 → 据此手动定 retrieve.py 的 THRESHOLD，并留存曲线
python -m scripts.sweep_threshold --lo 0.40 --hi 0.80 --step 0.05

# 4. 规则命中率 / 误判率（无需 DB、LLM）
python -m scripts.eval_rules

# 5. 风险拦截召回 / 误升 high 率（无需 DB、LLM）
python -m scripts.eval_risk
```

第 4、5 步**不需要运行中的 Postgres/Redis，也不真调大模型或 17TRACK**（config 全有默认值，
纯函数不碰网络/DB）；但仍需**项目依赖已安装**。第 1-3 步需要完整环境（含 BGE-m3 与灌好的库）。

> 耦合备注：eval_rules/eval_risk 只用纯函数 `classify_by_rules` / `evaluate_risk_rules`，
> 本可零依赖运行，但目前被 classify.py / risk_gate.py 顶层的
> `from app.services.llm import ...` 拖入整条 config 依赖链。
> 若想在无依赖的 CI 里单独跑规则/风险评测，可把这两个纯函数下沉到一个不 import llm 的
> 模块——但那需要同步改 test_classify / test_risk_gate 里 6 处 monkeypatch 的打桩目标，
> 未在此改动（避免改动无法在当前环境验证的测试）。
