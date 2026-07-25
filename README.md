# 跨境电商多语种智能客服 Agent 系统

多语种（英语 / 西班牙语 / 印尼语）客服工单自动化系统。工单进入后经五节点 Agent
编排处理：**PII 脱敏 → 分类 → 检索 → 工具调用 → 生成 → 风险闸门**。高风险动作强制
人工审核，仅低风险白名单可自动出站。

> 配套设计论证见《跨境客服Agent_方案设计.md》。本文档是实施规格驱动的交付说明。

## 阶段进度

按实施规格第 13 节分阶段交付。S1–S10 已全部完成（按"一次性做完"指令一次性交付）。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **S1** | 项目骨架、docker-compose、配置、DB 模型、Alembic 首版迁移、`/health` | ✅ 完成 |
| S2 | 知识库构建脚本、BGE-m3 封装、pgvector 入库、检索服务、`eval_retrieval.py` | ✅ 完成 |
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

# 3. 执行迁移（含 pgvector 扩展启用，迁移内已自动 CREATE EXTENSION）
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

## 已知短板（如实记录，不隐瞒）

1. **PII 脱敏仅覆盖结构化字段**（邮箱、电话、信用卡、订单号、运单号、IP）。
   人名与地址**未覆盖**——正则对印尼语 / 西语人名地址召回极低，属已知短板。
2. **订单数据为 mock**，仅运单号真实。订单主体（金额、时间、SKU）为虚构。
3. **BGE-m3 在印尼语（id）上的检索效果预期低于英语（en）。** 这是预期结果，
   须按语种分别统计上报，不试图掩盖。
4. **知识库无自动更新机制**，人工修正样本仅供离线分析，不做自动回流。
5. **未做真实平台账号对接**（Amazon / Shopify 卖家后台），无法验证真实工单分布。

## 安全降级方向（刻意设计，不对称）

- **风险闸门自身异常 → 默认 `high`，拦截。** 风控失效的代价是资损与店铺绩效受损。
- **成本护栏（限流 / 配额）异常 → 放行并告警。** 护栏失效的代价是多花钱。

两者降级方向相反，因为代价不对称。此设计贯穿 S7 / S8 实现。

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
migrations/         Alembic（env.py 与首版迁移，含 pgvector 扩展启用）
```
