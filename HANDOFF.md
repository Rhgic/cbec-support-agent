# 交接说明（2026-07-25）

> 新会话从这里接。项目路径：`/Users/rhgic/job project/Cross-border e-commerce/cbec-support-agent`

## 一句话状态

六节点 Agent 全链路**已真跑通**（真 API + arq worker + 真实 DeepSeek），单测 **152 全绿**。
本轮补上了此前缺失的那把尺子——**端到端批量评测**（`scripts/eval_e2e.py`，n=100 三语种标注工单），
并据此把「`tools` 过度拦截」的决策从拍脑袋变成了改前/改后可比的数字：`auto_solve_rate` **17% → 28%**。

## ⚠️ 本轮推翻的两个此前结论（读旧文档前先看这里）

**1. 基线不是 50%，是 17%。**
旧文档里的 50% 出自 n=12 的手工抽样，那 12 条里 product 占了一半；而真实标注集里 product 只占 21%。
n=100 全量重跑，改前基线是 **17.0%**。50% 是小样本的意图分布假象。

**2. 根因不是「没提取到订单号」，是「提取到了假订单号」。**
旧文档诊断为「通用政策问题本来就没有订单号 → 记 `tool_errors`」。实测不成立：
`_extract_order_no` 的正则 `\b[A-Za-z][A-Za-z0-9]{5,21}\b` 会匹配**任何 6 字母以上的普通单词**，
57 条 logistics/return 工单里 **52 条**提取出了假订单号（`shipping` / `delivered` / `refund` / `ongkir`…），
拿去 `orders.get_by_no` 必然查不到 → 记 `tool_errors` → 触发风险规则 4。
「没提取到订单号」那条分支实际只覆盖 5 条。

> 这是 bug，不是策略取舍。**在修掉它之前，A/B/C 三个方案加的任何判定规则都轮不到执行**——
> 整场方案讨论建立在一个错误的病因上。是单测逼出来的：按真实工单文本写断言，
> `ongkir retur ditanggung siapa nih` 直接炸了（`ongkir` 被当成订单号）。

## 本轮改动

**已定案：方案 B**（按「问题是否需要订单事实」区分），已实现。

- `app/graph/nodes/tools.py`
  - `_ORDER_RE` 收窄为「字母开头 + 至少含一位数字」（真实形态 `CBEC2024NNNNN`）
  - 新增 `_needs_order_facts()`：**默认仍然拦截**，只有明确命中通用政策提问、
    且不含「我那一单」指代时才豁免。安全侧是默认值，豁免才需要自证——
    规则漏写一条的后果是「多转一次人工」，而不是「拿通用政策去答具体订单」
- `scripts/eval_e2e.py`（新）：端到端批量评测。核心输出不是那一个百分比，而是**拦截原因直方图**——
  每条没放行的工单被哪条规则拦下。只有这个分布能回答「自动解决率涨了，是真变好还是把该拦的也放了」。
  支持 `--compare` 出改前/改后差值表
- `tests/test_tools.py`（新）：93 条单测，用例**全部取自真实标注工单文本**，不自造顺手例子

## 实测：改前 vs 改后（n=100，同一把尺子，唯一变量是 tools.py）

| 指标 | before | after | Δ |
|---|---|---|---|
| `auto_solve_rate` | 17.0% | **28.0%** | +11pp |
| `grounded_rate` | 93.4% | 92.2% | −1.2pp |
| 短路率 | 8.0% | 8.0% | ＝ |
| `rule4_tool_error` | 53 | **39** | −14 |
| `llm_layer_judgement` | 17 | 30 | +13 |

按意图（after）：product 17/21、return 8/27（原 0）、logistics 3/30（原 0）、other 0/22
按语种（after）：en 9/34、es 10/33、id 9/33（无显著语种偏斜）

**安全侧没有让步**（逐条核对过）：新增自动放行 12 条，其中 11 条正是手工标注的通用政策问题、
全部带真实引用，**没有一条「我的订单在哪」被自动放行**。另有一进一出 2 条属 product 意图
（本次改动根本不经过该路径），是 LLM 跑间波动。

> `rule4_tool_error` 只降 14 而非 53：改动只豁免了真正的通用政策问题，
> 其余是数据集里客户确实没给单号——见下方天花板。

## 🎯 下一步最大杠杆：评测集本身卡住了指标（新发现）

**100 条标注工单里，含订单号的是 0 条。**

于是 ≥70% 的目标在当前数据集上**结构性地测不出来**，与代码好坏无关：

| 意图 | 条数 | 上限 | 原因 |
|---|---|---|---|
| product | 21 | 已达 17 | — |
| other | 22 | **0** | 规则 1 `intent=other` 恒拦，结构上不可能自动放行 |
| logistics + return | 57 | 约 15 | 无一条带订单号，凡需订单事实者必转人工 |

**天花板 ≈ 21 + 15 = 36%，现在 28%。**

所以 P0 扩样不是「多凑点样本」，而是**必须造带订单号的工单**——否则这套系统最主要的一条链路
（查单 → 拿真实订单状态 → 有据回答）在评测里永远跑不到成功路径，`auto_solve_rate` 这个指标
也就永远在衡量一个残缺的系统。这是接真实工单前最该补的一件事。

## 当前指标全表（合成数据，n 小，接真实工单后须重跑）

| 指标 | 值 |
|---|---|
| 检索短路阈值 | **0.45**（sweep 曲线拐点；0.60 会误拒 45.5%） |
| 高风险拦截召回 | **100%**（34 条风险标注；误升 high 17.6%） |
| 对抗集无依据回答率 | **0%**（24 条） |
| 规则前置命中 | **34%**（省下的 LLM 调用） |
| `auto_solve_rate` | **28%**（n=100；结构性上限 ~36%，见上） |
| `grounded_rate` | **92.2%**（71/77，分母＝走到生成的工单） |
| 检索召回 recall@5 | en/es/id 均 100%（⚠️ 库仅 15 chunk，偏乐观） |
| p50 端到端时延 | 12.3 s |
| 单测 | 152 全绿 |

## 已完成 / 未完成（对照 `UPGRADE_DESIGN.md`）

**已完成**：P4 风险层加固（全部）、P1 重排（两阶段检索）、P2 引用真实性校验、
P3 关键词扩充（24%→34%）、**端到端批量评测地基 + tools 过度拦截修复（本轮）**
**未完成**：P0 数据扩样（**首要**：须含订单号；risk_labeled 34 vs ≥100、retrieval_eval 22、adversarial 24 vs 各 ≥80）、
P1 查询改写/混合检索/分类别阈值、P2 有据性自检/分语种语气、P3 few-shot/规则-LLM 一致性、P5 反馈回流

## 怎么把环境跑起来

```bash
cd "/Users/rhgic/job project/Cross-border e-commerce/cbec-support-agent"
export TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 GRPC_ENABLE_FORK_SUPPORT=false GLOG_minloglevel=3

docker compose up -d db                                   # Postgres+pgvector（Redis 用本机 6379）
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/cbec-api.log 2>&1 &
nohup .venv/bin/arq app.tasks.worker.WorkerSettings       > /tmp/cbec-worker.log 2>&1 &
nohup .venv/bin/python -m http.server 8100 --directory web > /tmp/cbec-web.log 2>&1 &
```

- 控制台 http://localhost:8100 ｜ API http://127.0.0.1:8000/health
- 前端 `web/app.js` 顶部 `USE_MOCK=false` 已指向真实后端
- `.env` 里 `DEEPSEEK_API_KEY` 已填、`DEEPSEEK_MODEL=deepseek-v4-flash`（`deepseek-chat` 已下线）

跑批量评测（⚠️ **必须先停掉 arq worker**，Milvus Lite 单进程锁）：

```bash
.venv/bin/python -m scripts.eval_e2e --limit 2            # 快速回归，每语种每意图 2 条
.venv/bin/python -m scripts.eval_e2e --out runs/x.json --compare runs/after.json
```

## 环境坑（都已在代码里兜底，但换机器要注意）

1. `TOKENIZERS_PARALLELISM/OMP_NUM_THREADS/GRPC_ENABLE_FORK_SUPPORT` 必须在 import 前设，否则 worker fork 后段错误静默退出
2. **不能用 `MILVUS_URI` 这个环境变量名** —— pymilvus 全局 Config 会捡走并当 URI 解析；本项目用 `CBEC_MILVUS_URI`
3. Milvus Lite 是单进程嵌入式库：worker 持有时 CLI 开不了第二个连接（生产切 standalone 即消除）
4. Redis 镜像拉取可能失败；本机 6379 已有 Redis 可直接用

## 相关文档

- `UPGRADE_DESIGN.md` —— 升级方案 + 11 bug 实录 + 指标基线（主参考）
- `runs/before.json` / `runs/after.json` —— 本轮 A-B 评测原始结果（含每条工单的判定与拦截原因）
- `docs/index.html` —— 求职项目主页（可 GitHub Pages；占位待填：姓名/GitHub/简历链接）
- `web/` —— 演示控制台（浅色查验台 UI，工作台/知识库/指标三视图）
- `~/Documents/面试话术卡_跨境客服Agent.md` —— 面试话术
- `~/Documents/Obsidian Vault/求职知识库/05~08` —— 知识沉淀
