# 交接说明（2026-07-25）

> 新会话从这里接。项目路径：`/Users/rhgic/job project/Cross-border e-commerce/cbec-support-agent`

## 一句话状态

六节点 Agent 全链路**已真跑通**（真 API + arq worker + 真实 DeepSeek），单测 59 全绿，
四条路径（自动放行 / 扣留 / 短路拒答 / 失败安全）全部实测验证过。真跑过程挖出并修复 **11 个 bug**（详见 `UPGRADE_DESIGN.md` 顶部实录）。

## ⏸ 待你拍板的唯一决策（下一步的最大杠杆）

**`tools` 节点对通用政策问题过度拦截**，压住了自动解决率：

- 批量实测 n=12：**product 6/6 自动放行，return 0/3、logistics 0/3 全部转人工**
- 根因：return/logistics 会去查订单，但「跨境物流多久到」「几天内可退」这类通用政策问题**本来就没有订单号**
  → 记 `tool_errors` → 触发风险规则 4 → 强制 high。回复本身质量没问题（有真实引用、内容准确）
- 修掉后 `auto_solve_rate` 有望从 **50% → ~92%**

三个方案（`UPGRADE_DESIGN.md` §1 有详表）：
- **A** 无订单号视为「不适用」，不记 error —— 自动解决率大涨，但「我的订单在哪」会被自动回通用政策答复
- **B** 按「问题是否需要订单事实」区分 —— 兼顾，需加判定规则（倾向此案）
- **C** 维持现状 —— 最保守，锁死 ~50%

> 这是「少雇几个客服」与「绝不答错具体订单」之间的定价，属业务决策。

## 当前实测指标（合成数据，n 小，接真实工单后须重跑）

| 指标 | 值 |
|---|---|
| 检索短路阈值 | **0.45**（sweep 曲线拐点；0.60 会误拒 45.5%） |
| 高风险拦截召回 | **100%**（34 条风险标注；误升 high 17.6%） |
| 对抗集无依据回答率 | **0%**（24 条） |
| 拒答率 | **7.7%**（78 条真实问题） |
| 规则前置命中 | **34%**（省下的 LLM 调用） |
| `auto_solve_rate` | ⚠️ **50%**（6/12，受上述过度拦截压制） |
| `grounded_rate` | ✅ **91.7%**（11/12） |
| 检索召回 recall@5 | en/es/id 均 100%（⚠️ 库仅 15 chunk，偏乐观） |

## 已完成 / 未完成（对照 `UPGRADE_DESIGN.md`）

**已完成**：P4 风险层加固（全部）、P1 重排（两阶段检索）、P2 引用真实性校验、P3 关键词扩充（24%→34%）
**未完成**：P1 查询改写/混合检索/分类别阈值、P2 有据性自检/分语种语气、P3 few-shot/规则-LLM 一致性、P5 反馈回流、P0 数据扩样未达标（risk_labeled 34 vs 目标≥100；retrieval_eval 22、adversarial 24 vs 目标各≥80）

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

## 环境坑（都已在代码里兜底，但换机器要注意）

1. `TOKENIZERS_PARALLELISM/OMP_NUM_THREADS/GRPC_ENABLE_FORK_SUPPORT` 必须在 import 前设，否则 worker fork 后段错误静默退出
2. **不能用 `MILVUS_URI` 这个环境变量名** —— pymilvus 全局 Config 会捡走并当 URI 解析；本项目用 `CBEC_MILVUS_URI`
3. Milvus Lite 是单进程嵌入式库：worker 持有时 CLI 开不了第二个连接（生产切 standalone 即消除）
4. Redis 镜像拉取可能失败；本机 6379 已有 Redis 可直接用

## 相关文档

- `UPGRADE_DESIGN.md` —— 升级方案 + 11 bug 实录 + 指标基线（主参考）
- `docs/index.html` —— 求职项目主页（可 GitHub Pages；占位待填：姓名/GitHub/简历链接）
- `web/` —— 演示控制台（浅色查验台 UI，工作台/知识库/指标三视图）
- `~/Documents/面试话术卡_跨境客服Agent.md` —— 面试话术
- `~/Documents/Obsidian Vault/求职知识库/05~08` —— 知识沉淀
