# 跨境电商多语种智能客服 Agent 系统

面向美国 / 墨西哥 / 印尼市场的多语种（英语 / 西班牙语 / 印尼语）客服工单自动化系统。
工单进入后经六节点 Agent 编排处理：**PII 脱敏 → 分类 → 检索 → 工具调用 → 生成 → 风险闸门**。
高风险动作强制人工审核，仅低风险白名单可自动出站。

> 这套系统的难点不是让模型会答，而是让它**知道什么时候不该答**：
> 跨境客服答错一句要赔钱，所以「查不到依据就拒答」和「涉及金额一律转人工」
> 是比自动解决率更硬的约束。下面每个数字都可复现，包括不好看的那些。

## 实测结果

全部来自 `scripts/eval_*.py` 的可复现评测，数据集在 `datasets/`。

| 指标 | 值 | 口径 |
|---|---|---|
| 自动解决率 | **35.0%**（56/160） | 带订单号子集 41.7%（25/60） |
| 高风险拦截召回 | **100%**（47/47） | 100 条风险标注集；误升 high 5.7% |
| 有据回答率 | **84.4%**（108/128） | 分母＝走到生成的工单 |
| 检索短路阈值 | **0.45** | 112 条有答案集误拒 **0.9%**；84 条对抗集暴露 3.6% |
| 分语种召回 recall@5 | en 39/39 · es 37/37 · id 36/36 | ⚠️ 知识库仅 15 chunk，偏乐观 |
| 规则前置命中 | **46.9%**（75/160） | 省去该比例工单的意图分类 LLM 调用 |
| p50 端到端时延 | **12.8s** | 串行、含真实 LLM 调用，非并发压测下的延迟 |
| 并发压测 | 7.67 QPS | Locust 50 用户 / 2min；成功 1.53 QPS，80.1% POST 被限流护栏按预期 429 |
| 单测 | **184 全绿** | |

> ⚠️ 均为合成标注集，n 偏小；接入真实工单后须整体重跑。

**一个值得说的过程**：自动解决率长期上不去，最初判断是「风险策略太保守」。
写了端到端评测并输出**拦截原因直方图**后才发现是 bug——订单号提取正则
`\b[A-Za-z][A-Za-z0-9]{5,21}\b` 会匹配任何 6 字母以上的普通单词，
57 条物流/退货工单里 **52 条**把 `shipping`、`refund` 当成订单号去查库，
查不到即记工具失败、强制转人工。修复后同一评测集 **17% → 28%**。
是数据推翻了原先的判断，不是靠猜。详见 `UPGRADE_DESIGN.md`。

> 配套设计论证见《跨境客服Agent_方案设计.md》。本文档是实施规格驱动的交付说明。
> 项目主页（含架构图与取舍说明）：`docs/index.html`。

## 阶段进度

按实施规格第 13 节分阶段交付。S1–S10 已全部完成（按"一次性做完"指令一次性交付）。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **S1** | 项目骨架、docker-compose、配置、DB 模型、Alembic 首版迁移、`/health` | ✅ 完成 |
| S2 | 知识库构建脚本、BGE-m3 封装、Milvus 入库、检索服务、`eval_retrieval.py` | ✅ 完成 |
| S3 | LangGraph 骨架 + mask + classify 两节点，PG checkpointer | ✅ 完成 |
| S4 | retrieve 节点 + 短路机制 | ✅ 完成 |
| S5 | 工具层三件（tracking / orders / return_policy）+ tools 节点 | ✅ 完成 |
| S6 | generate 节点 + 引用 + PII 还原 | ✅ 完成 |
| S7 | risk_gate 规则层 + LLM 层 + 动作映射 | ✅ 完成 |
| S8 | 护栏（限流/配额/熔断/缓存）+ 降级 + arq 异步 + 可观测 | ✅ 完成 |
| S9 | 审核队列 API + 极简前端 | ✅ 完成 |
| S10 | 全量埋点 + 指标聚合接口 + 压测 | ✅ 完成 |

## 快速开始

```bash
# 1. 启动依赖
docker compose up -d db redis

# 2. 安装依赖（Python 3.12+）
pip install -e .

# 3. 执行 PG 业务库迁移（历史回退列仍依赖 pgvector 扩展）
alembic upgrade head

# 4. 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. 启动 arq 异步工单处理 worker（另开一个终端）
arq app.tasks.worker.WorkerSettings

# 6. 健康检查（各依赖状态）
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

`/health` 在 PG / Redis 不可用时仍返回 HTTP 200，整体状态为 `degraded`，并在
`dependencies` 中标注每个依赖的 `ok` / `down` / `configured` / `no_api_key`。

## 实时会话接入演示

`scripts/demo_server.py` 额外提供一个**通用平台接入层**：平台只要把新消息转换为
`user_id + message + channel` 并 POST 到 `/webhook/messages`，服务就会按 `user_id`
带入最近 6 条已脱敏历史运行 Agent；结果通过 SSE 推送到已打开的客服界面。

```bash
# 启动实时演示服务（控制台会自动订阅 /events）
DEEPSEEK_API_KEY=sk-xxx TOKENIZERS_PARALLELISM=false \
  .venv/bin/uvicorn scripts.demo_server:app --port 8100

# 模拟任意客服平台推送一条客户消息
curl -X POST http://127.0.0.1:8100/webhook/messages \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"customer_001","channel":"shopify","message":"My tracking has not updated for five days."}'
```

打开 `http://127.0.0.1:8100/` 后，新消息会自动进入工单列表、选中该用户的会话并展示
回复建议。该演示接入层使用内存会话与 query token；生产接入前须替换为 Redis / 数据库、
平台验签和短期会话令牌。当前尚未对接真实 Shopify、Amazon 或 WhatsApp 账号。

## 已知短板（如实记录，不隐瞒）

1. **PII 脱敏仅覆盖结构化字段**（邮箱、电话、信用卡、订单号、运单号、IP）。
   人名与地址**未覆盖**——正则对印尼语 / 西语人名地址召回极低，属已知短板。
2. **订单数据为 mock**，仅运单号真实。订单主体（金额、时间、SKU）为虚构。
3. **BGE-m3 在印尼语（id）上的检索效果预期低于英语（en）。** 这是预期结果，
   须按语种分别统计上报，不试图掩盖。
4. **知识库无自动更新机制**；人工纠正可以离线导出，但必须复核后手动合并，不做线上自动学习。
5. **未做真实平台账号对接**（Amazon / Shopify 卖家后台），无法验证真实工单分布。

## 安全降级方向（刻意设计，不对称）

- **风险闸门自身异常 → 默认 `high`，拦截。** 风控失效的代价是资损与店铺绩效受损。
- **成本护栏（限流 / 配额）异常 → 放行并告警。** 护栏失效的代价是多花钱。

两者降级方向相反，因为代价不对称。此设计贯穿 S7 / S8 实现。

## 人工反馈离线回流

审核接口可选提交 `corrected_lang`、`corrected_intent`、`corrected_risk_level`。系统只记录
人工明确纠正的字段；离线导出时只使用脱敏文本，不读取原始客户消息。

```bash
alembic upgrade head
python -m scripts.export_feedback --out-dir runs/feedback_export
```

导出结果是候选样本，人工复核后再合并到 `datasets/`，不会自动进入线上规则或模型。

## 目录结构（节选自实施规格第 3 节）

```
app/
├── main.py / config.py / database.py / models.py / schemas.py / deps.py / observability.py
├── routers/        health.py / tickets.py / review.py / knowledge.py
├── graph/          state.py / builder.py / nodes/（mask / classify / retrieve / tools / generate / risk_gate）
├── services/       llm / embedding / vectorstore / pii / guardrails
├── tools/          tracking / orders / return_policy
├── metrics/events.py
└── tasks/worker.py
migrations/         Alembic（PG 业务数据与知识文本/元数据；向量检索由 Milvus 承担）
```
