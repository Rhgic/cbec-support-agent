# 跨境电商多语种智能客服 Agent — 核心功能升级设计方案

> ## 🔎 端到端联调实录（2026-07-25，真跑 API + arq worker + 真实 LLM 暴露的 11 个 bug）
> 单测 59 全绿、六节点全链路跑通、trace 完整、四条路径（自动放行/扣留/短路/失败安全）全部验证。
> 以下 bug **只在真跑时暴露**，种子数据与单测一个都碰不到：
>
> **第一轮 · 起 API + worker（无 LLM key）**
> 1. `PostgresSaver` 不认 SQLAlchemy 方言串 `postgresql+psycopg://` → 剥前缀（`_libpq_dsn`）
> 2. `MILVUS_URI` 环境变量被 **pymilvus 全局 Config 捡走**，把本地文件路径当 URI 解析报 `Illegal uri` → 改名 `CBEC_MILVUS_URI` + Field alias
> 3. Milvus Lite 相对路径在 worker 工作目录下失效 → 转绝对路径
> 4. worker fork 子进程加载 torch/gRPC 段错误静默退出 → 在 import 前设 `TOKENIZERS_PARALLELISM/OMP_NUM_THREADS/GRPC_ENABLE_FORK_SUPPORT`
> 5. `db.close()` 后访问 ORM 对象属性 → `DetachedInstanceError` → 改读编排结果 dict
> 6. **`record_agent_run` 从未被调用**，trace 恒为空（规格第 10 节埋点等于没实现）→ builder 加 `_traced` 装饰器统一包装 6 个节点
> 7. **Redis 客户端无超时**，Redis 故障时把整个请求拖死（护栏成了故障放大器，与"降级优先于保护"背道而驰）→ 加 `socket_connect_timeout/socket_timeout=1`
>
> **第二轮 · 接入真实 DeepSeek 后（这四个前七个都掩盖不住，但没 key 时全被兜底模板遮住）**
> 8. **模型名 `deepseek-chat` 已下线**（现为 `deepseek-v4-pro/flash`），所有 LLM 调用 400 → 全部走兜底模板
> 9. **`generate_prompt` 从未传入客户的问题**，只喂了知识库片段与工具结果 → 模型不知道客户问了什么，只会回「请描述你的问题」
> 10. **检索片段拼接时丢掉 `source_url`** → 模型无从引用 → `citations` 恒空 → 被 P2 引用校验判为「有检索却无引用」强制升 high
> 11. **`tools` 节点先提取订单号再判意图**，与规格「product 不调工具」矛盾 → 所有不带订单号的产品/政策咨询被记 `tool_errors` → 触发风险规则 4 **全部误拦，自动解决率归零**
>
> 第 11 个影响最大：修复前后同一条工单从 `high/human_required` → `low/auto_send`。
>
> **实测记录**
> - 降级红线（Redis 不可达）：`/health` **200**/29ms 如实报 `redis: down`；成本护栏**放行不拦**；`POST /tickets` 因队列确实不可用返回 **503 而非假装成功**
> - 护栏：无 token → 401；限流**精确在第 20 次**转 429（配置 20/min）
> - 短路机制（真 Milvus 分数）：库里有答案 0.596 / 0.642 → 放行；库里无答案 0.406 / 0.413 → **短路拒答**。阈值 0.45 干净分开两类
> - 风险规则：10 条变体用例全判对，含否定豁免（`we do not guarantee` 不算承诺）与币种变体（`50 dollars` / `30 USD`）
> - PII 端到端：真实回复中保留 `[TRACKING_1]` 占位符 —— **LLM 全程未见过真实订单号**
> - 单条工单成本：v4-flash 下 trace 显示 `$0.00000`（四舍五入）
>
> **已知运行约束**：Milvus Lite 为单进程嵌入式库，worker 持有时 CLI 无法开第二个连接；生产切 standalone 即消除（`CBEC_MILVUS_URI` 可切换设计的价值）。

> 版本 v1.0 ｜ 日期 2026-07-25 ｜ 范围：**只攻主功能**（分类/检索/生成/风险 四个核心节点 + 数据地基 + 反馈回流）。
> 明确**不做**：A/B 实验框架、多 LLM 冗余、MLOps 重训管线、监控看板、多轮对话（见 §6 边界）。

---

## 1. 升级目标（先定"可衡量"的靶子）

系统的核心价值 = **在"安全"前提下，把"自动解决率"提到尽可能高**。
因此所有升级都挂在 6 个可测指标上，改之前先出基线、改之后比数字。

| 指标 | 定义 | 测量方式 | 现状基线 | 升级目标 |
|---|---|---|---|---|
| `auto_solve_rate` | action=auto_send 占比 | `eval_e2e`（n=100） | **28%**（改前 17%）——按意图：product 17/21、return 8/27、logistics 3/30、other 0/22 | ≥0.70 ⚠️ 当前评测集**结构性测不出**，上限 ~36%，见下方「首要杠杆」 |
| `intent_accuracy` | 意图分类正确率 | `eval_rules` + LLM 层抽检 | 规则命中 34%，命中内 85%（误判 15%）；整体待 LLM 层 | ≥0.85 |
| `retrieval_recall@5` | 分语种召回率 | `eval_retrieval` | ✅ en/es/id 均 100%（库扩至 15 chunk + rerank 后复测，⚠️ 库仍小，偏乐观） | en≥0.90 / es≥0.85 / id≥0.80 |
| `grounded_rate` | 有据回答率（有引用且引用真实） | `eval_e2e`（n=100） | **92.2%**（71/77，分母＝走到生成的工单）；编造 URL 检出 100% | ≥0.95 |
| `risk_recall` | 高风险拦截召回 | `eval_risk`（risk_labeled） | ✅ **100%**（P4 后，合成 34 条；误升 high 17.6%） | ≥0.95 |
| `misjudge_rate` | 规则命中样本内误判率 | `eval_rules` | 15%（待 P3 降到 ≤5%） | ≤0.05 |

> ⚠️ 指标基于合成数据集（n 均较小），接入真实工单后须重跑复核。

### ✅ 已结：`tools` 对通用政策问题的过度拦截（取方案 B，已实现）

> 本节此前的诊断被实测推翻，保留修正记录——**病因错了，整场方案讨论就建立在沙上**。

**推翻的旧结论 1：基线不是 50%。**
50% 出自 n=12 手工抽样，那 12 条里 product 占一半；真实标注集里 product 只占 21%。
n=100 全量重跑，改前基线 **17.0%**。

**推翻的旧结论 2：根因不是「没提取到订单号」，是「提取到了假订单号」。**
`_extract_order_no` 的正则 `\b[A-Za-z][A-Za-z0-9]{5,21}\b` 匹配**任何 6 字母以上的普通单词**——
57 条 logistics/return 工单里 **52 条**提取出假订单号（`shipping`/`delivered`/`refund`/`ongkir`…），
拿去 `orders.get_by_no` 必然查不到 → `tool_errors` → 风险规则 4。
「没提取到订单号」那条分支实际只覆盖 5 条。**这是 bug，不是风险策略取舍**；
不修它，A/B/C 任一方案加的判定规则都轮不到执行。

**落地**：`_ORDER_RE` 收窄为「字母开头 + 至少含一位数字」；新增 `_needs_order_facts()` 实现方案 B——
**默认仍然拦截**，只有明确命中通用政策提问且不含「我那一单」指代时才豁免。
方向刻意不对称：漏写一条规则的后果是「多转一次人工」，而非「拿通用政策去答具体订单」。

**实测（n=100，唯一变量是 `tools.py`）**：`auto_solve_rate` 17.0% → **28.0%**，
`rule4_tool_error` 53 → 39，`grounded_rate` 93.4% → 92.2%。
新增自动放行 12 条中 11 条为通用政策问题且全部带真实引用，**无一条「我的订单在哪」被放行**。

### 🎯 新的首要杠杆：评测集本身卡死了指标

**100 条标注工单里，含订单号的是 0 条。** ≥70% 的目标在当前数据集上**结构性地测不出来**：

| 意图 | 条数 | 上限 | 原因 |
|---|---|---|---|
| product | 21 | 已达 17 | — |
| other | 22 | **0** | 规则 1 `intent=other` 恒拦，结构上不可能自动放行 |
| logistics + return | 57 | 约 15 | 无一条带订单号，凡需订单事实者必转人工 |

**天花板 ≈ 36%，现在 28%。** 所以 P0 扩样的第一要务不是"凑样本量"，而是**造带订单号的工单**——
否则本系统最主要的一条链路（查单 → 拿真实订单状态 → 有据回答）在评测里永远跑不到成功路径，
`auto_solve_rate` 也就永远在衡量一个残缺的系统。

---

## 2. 升级总览（只 4 个核心节点 + 2 个地基）

```
            ┌─ P0 数据地基（扩标注集→出真基线） ─┐
            │                                  │
 mask → [classify] → [retrieve] → tools → [generate] → [risk_gate] → END
            ▲ P3         ▲ P1        ▲         ▲ P2        ▲ P4
            │            │           │         │           │
            └─ P5 人工反馈回流（review 修正 → 反哺规则/prompt/知识库） ─┘
```

- **P1 检索**：自动解决率的最大杠杆——检索短路太多，后面全白搭。
- **P2 生成**：让回复"有据可查"，减少误判放行。
- **P3 分类**：意图错 → 工具错 → 全错。
- **P4 风险**：关键词规则脆，遇到变体就漏/误判。
- **P0/P5 地基**：没数据没基线，没回流不进步。

---

## 3. 升级点详案（现状 → 问题 → 方案 → 改动文件 → 验收）

### P0 数据地基（前置，阻塞一切验收）
- **现状**：6 个标注集均为 ≤50 条种子模板（`datasets/README.md` 自己注明"需扩充"）。
- **问题**：`intent_accuracy`/`risk_recall`/`retrieval_recall` 全是种子数字，无法证明系统真能用。
- **方案**：
  0. **（本轮实测新增，最高优先）`tickets_*.jsonl` 必须含带订单号的工单**——当前 0/100 条带单号，
     导致「查单 → 拿真实订单状态 → 有据回答」这条主链路在评测里永远跑不到成功路径，
     `auto_solve_rate` 结构性上限被锁在 ~36%。这不是样本量问题，扩到 1000 条也不会变好。
  1. `tickets_{en,es,id}.jsonl` 各扩到 **≥100 条**，覆盖真实表达（口语、错别字、缩写、混合语种）。
  2. `risk_labeled.jsonl` 扩到 **≥100 条**，覆盖：金额变体、承诺变体、无依据、工具失败、正常放行。
  3. `retrieval_eval.jsonl` + `adversarial.jsonl` 各扩到 **≥80 条**。
  4. 跑 `eval_rules` / `eval_risk` / `eval_retrieval` / `sweep_threshold` 出真基线，写进本文件 §1 表格。
- **改动文件**：仅 `datasets/*.jsonl`（数据，不动代码）。
- **验收**：6 个指标有非种子基线值。

### P1 检索升级（核心中的核心）  ⏳ 重排已完成（2026-07-25）
> ✅ **两阶段检索**：向量召回 `recall_k=20` → BGE-reranker-v2-m3 交叉编码精排 → top-5（`services/rerank.py`，`RERANK_ENABLED` 可关）。
> 实证价值：查询"包裹没到"时纯向量把**不相关的"电池"文档**排首位（cosine 0.66 最高），reranker 把它压到末位、把物流文档提到第一——**修正了向量粗排的错误**。
> 边界处理：短路判定改用「候选内最大**向量**分」（`retrieve.py` / `sweep_threshold.py`），**rerank 只改进生成的 chunk 与顺序，不改已校准的 0.45 阈值语义**。
> 落地坑：`FlagReranker` 在新版 transformers 上报 `XLMRobertaTokenizer has no attribute prepare_for_model` → 改用 transformers 的 `AutoModelForSequenceClassification` 直连。
> 知识库同步扩容 9 → **15 chunk**（每类目 5 篇）；扩库后重跑 sweep，**0.45 仍是拐点**（阈值稳健，非小库过拟合）。
> ⏳ **未做**：查询改写、混合检索（BM25/稀疏）、分类别阈值。
- **现状(原)**：
- **现状**（`app/services/vectorstore.py` / `app/graph/nodes/retrieve.py`）：
  Milvus 单向量检索，`k=5`、`category` 过滤、COSINE、全局 `THRESHOLD=0.45`、无改写、无重排。
- **问题**：查询是原始工单（长、口语、含订单号噪声）；中文知识库被多语种查询；单阈值一刀切。
- **方案（按性价比排序）**：
  1. **查询改写**（`retrieve` 前置一步）：把长工单压成"核心问题 + 意图关键词"。
     - 便宜版：规则抽取（复用 classify 关键词表 + 订单/运单号实体）。
     - 提质版：一次轻量 LLM rewrite（成本可控，仅改写不调长链）。
  2. **混合检索**：向量召回 + 关键词（BM25/实体词）重打分。订单号、SKU、产品名这类**精确词**纯向量召回差，关键词互补。
     - 落地：Milvus 稀疏向量 `hybrid_search`，或 PG `tsvector` 兜底。
  3. **重排（rerank）**：召回 `k=20` → BGE-reranker-v2-m3 重排取 top-5。跨语种同族模型，CPU 上 20 对重排毫秒级，精度提升明显。
  4. **分类别阈值**：`sweep_threshold` 已能出分类别曲线 → 把全局 `THRESHOLD` 改成 `{intent: threshold}`。
- **改动文件**：`vectorstore.py`（加 hybrid/rerank）、`retrieve.py`（查询改写 + 分类别阈值）、新增 `services/rerank.py`。
- **验收**：`retrieval_recall@5` 达 §1 目标；对抗集短路率不降（不误放）。

### P2 生成有据性  ⏳ 便宜那半已完成（2026-07-25）
> ✅ **引用真实性校验**：`citations` 的 URL 必须 ∈ 本次检索返回的 `source_url` 集合，否则视为编造 → 升 high 并剔除假引用。纯函数 `_fabricated_citations`（`generate.py`），**编造 URL 检出 100%**（无需 LLM 即可验）。
> ⏳ **未做**：抽事实逐条核对（下方方案 2）——全案最脆、性价比最低，暂缓。
- **现状**（`app/graph/nodes/generate.py`）：有 chunk 就必须有 citation，否则兜底 + 升 high。
- **问题**：**有引用 ≠ 有据**——LLM 可以"引用了但编造内容"，或编造不存在的 source_url。
- **方案**：
  1. **引用真实性校验**：`citations` 里的 URL 必须 ∈ 本次检索返回的 `source_url` 集合；否则视为幻觉 → 升 high。
  2. **有据性自检**：抽取回复中的关键事实（数字、日期、政策词、订单状态），逐一核对是否出现在 `retrieved`/`tool_info` 里；出现"无中生有"的事实 → 降兜底 + 升 high。
  3. **分语种语气模板**：es 正式、id 友好、en 简洁，写进 `generate_prompt` 的 system。
- **改动文件**：`generate.py`（加校验）、`llm.py`（prompt 模板）。
- **验收**：`grounded_rate ≥0.95`；编造 URL 检出率 100%。

### P3 分类鲁棒性
- **现状**（`app/graph/nodes/classify.py`）：三语种关键词规则 → LLM 零样本兜底。
- **问题**：关键词漏判变体/俚语；零样本 LLM 准确率上限低；错了不学习。
- **方案**：
  1. **关键词扩充**：用 `eval_rules` 找出低召回的 intent，按语种补同义词/俚语/常见拼写错误。
  2. **few-shot 分类 prompt**：`classify_prompt` 由零样本 → 每 intent 每语种 2~3 个示例，准确率通常 +5~15%。
  3. **规则/LLM 一致性**（轻量）：规则命中时也记录 LLM 会判什么（异步、不计费阻塞），二者分歧 → 进审核池供规则修订。
- **改动文件**：`classify.py`（关键词表）、`llm.py`（few-shot prompt）。
- **验收**：`intent_accuracy ≥0.85`、`misjudge_rate ≤0.05`。

### P4 风险层加固  ✅ 已完成（2026-07-25）
> 已实现：**金额归一化**（`$50` / `50 USD` / `50 dollars` / `50 bucks` 皆命中）、承诺**否定豁免**（"we do not guarantee" / "No garantizamos" 不再误判）、新增**敏感动作**规则（改地址/改支付/索要验证码 → high，防账户接管）。
> 实测（合成 34 条，含新增变体用例）：`risk_recall` 76.5% → **100%**，误升 high 29.4% → **17.6%**（剩余为合法报价的已知代价，见 §5.4）。
> 改动：`risk_gate.py` 的 `_AMOUNT_RE` + 新增 `_NEG` / `_SENSITIVE_RE` / `_has_unnegated_promise`。
- **现状**（`app/graph/nodes/risk_gate.py`）：6 条确定性规则，金额/承诺靠正则。
- **问题**：
  - 金额正则：`$50` 命中但 `50 dollars`、`50 bucks`、`USD 50` 漏。
  - 承诺正则：`we do **not** guarantee` 误判成承诺（无否定豁免）。
  - 缺敏感动作检测（改地址/改支付/索要凭证）。
- **方案**：
  1. **金额归一化**：先把 `50 dollars/50 USD/$50/50 bucks` 归一到统一形式再匹配。
  2. **否定豁免**：承诺词前若干词窗口内出现 `no/not/no garantizamos/tidak` → 不计承诺。
  3. **敏感动作规则**：草稿含"改地址/改支付方式/提供验证码"类 → high。
- **改动文件**：`risk_gate.py`（`_AMOUNT_RE`、`_PROMISE_RE`、新增敏感动作表）。
- **验收**：`risk_recall ≥0.95`；否定句误判率 ≤0.02。

### P5 人工反馈回流（让系统"越用越好"）
- **现状**：`review` 修正已落库，但**没有回流**——错了不改，系统停在出厂水平。
- **方案**（轻量，不搞自动重训）：
  1. review 的 `failure_tags` + 修正后的 `final_reply` 定期导出成新的标注样本，追加进 `datasets/`。
  2. 意图被人工改判的工单 → 转成 `tickets_{lang}.jsonl` 新样本 → 反哺规则表/few-shot 示例。
  3. 高风险漏判案例 → 转成 `risk_labeled.jsonl` → 反哺风险规则。
- **改动文件**：`scripts/export_feedback.py`（新增）、`routers/review.py`（打标）。
- **验收**：每月新增标注 ≥50 条，基线指标随数据增长可见提升。

---

## 4. 分阶段计划（每阶段可独立验收）

| 阶段 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| **U0** | P0 数据扩样 + 出真基线 | 无 | 6 指标有真数 |
| **U1** | P1 检索（改写+混合+重排+分类别阈值） | U0 | ⏳ 重排已完成、recall@5 100%；改写/混合/分类别阈值待做 |
| **U2** | P2 生成有据性（引用校验+自检+语气） | U1 | grounded_rate≥0.95 ⏳ 引用校验已上，自检/语气待做 |
| **U3** | P3 分类（扩词+few-shot+一致性） | U0 | intent_acc≥0.85 |
| **U4** | P4 风险（归一化+豁免+敏感动作） | U0 | ✅ risk_recall 100%（合成集，已完成） |
| **U5** | P5 反馈回流（导出+反哺） | U1–U4 | 月增≥50 标注 |

> U1/U2 串行（生成依赖检索质量）；U3/U4 可与 U1 并行；U5 最后接。

---

## 5. 风险与权衡（诚实写）

1. **重排/LLM 改写会增加时延与成本**：改写 +1 次轻量 LLM 调用，重排 +CPU 毫秒级。需用 `node_latency_seconds` 盯 P99，超预算就把"LLM 改写"降级为"规则抽取"。
2. **混合检索提升召回但可能抬高误放**：关键词命中过宽会拉低精度 → 用 `sweep_threshold` 对混合分数重新校准阈值。
3. **few-shot 增大 prompt token**：成本随之上升，需纳入 `cost_total_usd` 监控，超 `breaker_cost_threshold_usd` 自动熔断。
4. **规则加固有"过严"风险**：承诺豁免/金额归一化若误伤正常回复，会压低 `auto_solve_rate` → 用 `eval_risk` 的"误升 high 率"反向校验。

---

## 6. 明确不做（边界，防止跑偏）

- ❌ A/B 实验框架、多 LLM 冗余、自动重训管线、监控看板/告警 infra。
- ❌ 多轮对话 / session 记忆：是真实客服的方向，但属"大改架构"，需单独立项，不在本轮"主功能加固"范围。
- ❌ 真实外发（平台对接/真实发信）：规格 1.2 本就定义为"只起草不发送"。
- 判断标准：**不直接提升"自动解决率 / 有据率 / 风险召回"的，本轮都不做。**
