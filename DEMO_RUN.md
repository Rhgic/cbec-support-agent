# 本地 live demo 运行说明（面试屏幕共享用）

一条命令起服务，浏览器现场输任意工单，真跑「脱敏 → 分类 → Milvus 检索 → 工具 → 生成 → 风险闸门」。

## 一次性准备（已做过可跳过）

```bash
cd "跨境客服项目根目录"

# 1. 起数据库（pgvector）
docker compose up -d db

# 2. 建表 + 灌知识库到 Milvus
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/cbec"
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.build_knowledge --all
TOKENIZERS_PARALLELISM=false .venv/bin/python -m scripts.embed_knowledge

# 3. 播种 mock 订单（tools 才查得到）
.venv/bin/python -m scripts.seed_orders --n 12
```

## 每次演示前起服务

```bash
cd "跨境客服项目根目录"
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/cbec"
export DEEPSEEK_API_KEY="sk-你自己的key"      # ← 填这个，generate 才出真实回复
export TOKENIZERS_PARALLELISM=false

.venv/bin/uvicorn scripts.demo_server:app --host 127.0.0.1 --port 8100
```

浏览器打开 **http://localhost:8100/** —— 右上角会显示「实时后端」绿灯（不是「样例数据」）。
在「新工单」框里输任意语言的工单，点「运行 Agent」，右侧真跑出语种/意图/检索分数/风险裁决/回复。

## 演示脚本建议（按打动面试官排序）

1. **短路拒答**（最能体现护栏）：输一句知识库答不了的，如
   `我想问下你们老板是谁` —— 看检索分数低、短路、不编造、转人工。
2. **多语种 + 高风险拦截**：西语退款
   `quiero un reembolso, mi pedido CBEC202400002 llego roto` —— 看识别 es、检索跨语种、涉及退款转人工。
3. **正常问题自动发送**（需 key）：物流查询
   `where is my order CBEC202400001` —— 看规则分类、订单查到、生成真实回复、风险 low、自动发送。

## 关于 key（诚实说明）

- **有 DEEPSEEK_API_KEY**：generate 出真实回复；LLM 分类和 LLM 风险层生效 → 完整体验。
- **没 key**：mask / 规则分类 / Milvus 检索 / 规则风险层仍是**真的**；只有 generate 走兜底（转人工）。
  这不是 bug，是刻意的优雅降级——面试时可以两种都演，正好讲「LLM 不可用时系统不崩、退回人工」。

## 说明

- demo 服务同步内联跑真实管道（不依赖 arq/worker），伺服 app.js 时把 USE_MOCK 临时置 false，
  源文件不改（GitHub Pages 那份仍是样例模式）。
- 第一条工单会稍慢（加载 BGE-m3），之后很快；服务启动时已预热。
