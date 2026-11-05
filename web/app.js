/* 跨境客服 · Agent 控制台 —— 演示前端（干净中性仪表盘版）
 *
 * 默认走 SAMPLE 数据（结构与后端 API 完全一致），无需 LLM key / worker 即可演示。
 * USE_MOCK 改 false 即切真实后端：createTicket → 轮询 getTicket → getTrace，渲染函数不变。
 * 文案用中文；后端真实字段值（节点名 / action / 语种码 / rule·llm）保留英文等宽。
 */
// 静态页面默认明确使用样例模式；scripts/demo_server.py 同源伺服时会替换为 false。
let USE_MOCK = true;
const API = { base: window.location.origin, token: "dev-token" };
const NODES = ["mask", "classify", "retrieve", "tools", "generate", "risk_gate"];
const NODE_CN = { mask: "脱敏", classify: "分类", retrieve: "检索", tools: "工具", generate: "生成", risk_gate: "风险闸门" };
const VERDICT = {
  low:  { head: "已自动处理", action: "auto_send",      tag: "回复已生成，待发送" },
  mid:  { head: "等待快速确认", action: "quick_review",   tag: "需要确认" },
  high: { head: "等待你的审核", action: "human_required", tag: "需要审核" },
};
const LANG_CN = { en: "英语", es: "西语", id: "印尼语" };

/* ---------------- SAMPLE 数据（== TicketOut + trace） ---------------- */
const SAMPLES = {
  logi: {
    ticket: {
      ticket_id: 4817, status: "ready_to_send", lang: "en", intent: "logistics",
      intent_confidence: 0.91, intent_method: "rule", retrieval_score: 0.78, short_circuited: false,
      draft_reply: "Your parcel (SF1234567890) cleared the transit hub and is at the customs step. Cross-border delivery usually lands in 7–15 business days; you're on day 6, so it's on track. If tracking stalls past 5 days we'll open a carrier trace for you.",
      citations: ["file://logistics_faq.md"], risk_level: "low", action: "auto_send",
      customer_message: "Hi, where is my order? Tracking number is SF1234567890, it's been almost a week.",
      customer_translation_zh: "你好，我的订单到哪里了？运单号是 SF1234567890，已经快一周了。",
      draft_translation_zh: "您的包裹（SF1234567890）已通过中转中心，目前处于清关阶段。跨境配送通常需要 7–15 个工作日；您现在是第 6 天，仍在正常时效内。如果物流超过 5 天没有更新，我们会为您向承运商发起查询。",
      tool_results: {
        order: { order_no: "CBEC202400001", product_name: "Travel Backpack", status: "shipped", tracking_no: "SF1234567890" },
        tracking: { events: [{ status: "Arrived at customs", time: "2026-07-22", location: "Shenzhen export hub" }] },
      },
    },
    trace: { runs: [
      { node: "mask", latency_ms: 12, ok: true },
      { node: "classify", latency_ms: 3, ok: true },
      { node: "retrieve", latency_ms: 41, ok: true },
      { node: "tools", latency_ms: 812, ok: true },
      { node: "generate", latency_ms: 1340, token_in: 320, token_out: 96, cost_usd: 0.00019, ok: true },
      { node: "risk_gate", latency_ms: 2, ok: true },
    ]},
  },
  refund: {
    ticket: {
      ticket_id: 4818, status: "awaiting_review", lang: "es", intent: "return",
      intent_confidence: 0.88, intent_method: "llm", retrieval_score: 0.71, short_circuited: false,
      draft_reply: "Lamentamos el problema. Según nuestra política, le haremos un reembolso de $50 a su método de pago original en un plazo de 3 a 5 días hábiles una vez recibido el artículo devuelto.",
      citations: ["file://return_policy.md"], risk_level: "high", action: "human_required",
      customer_message: "Quiero un reembolso por el artículo roto que recibí, ¿cuánto tardan?",
      customer_translation_zh: "我收到的商品坏了，想申请退款，需要多久？",
      draft_translation_zh: "很抱歉给您带来困扰。根据我们的政策，在收到退回的商品后，我们会在 3–5 个工作日内将 50 美元退款至您原来的付款方式。",
      tool_results: { tools_skipped: "未提供订单号；先由人工确认退款条件与订单信息" },
    },
    trace: { runs: [
      { node: "mask", latency_ms: 14, ok: true },
      { node: "classify", latency_ms: 940, token_in: 88, token_out: 24, cost_usd: 0.00005, ok: true },
      { node: "retrieve", latency_ms: 44, ok: true },
      { node: "tools", latency_ms: 205, ok: true },
      { node: "generate", latency_ms: 1521, token_in: 356, token_out: 120, cost_usd: 0.00023, ok: true },
      { node: "risk_gate", latency_ms: 1, ok: true, error: "规则：回复涉及金额/退款" },
    ]},
  },
  oos: {
    ticket: {
      ticket_id: 4819, status: "awaiting_review", lang: "id", intent: "other",
      intent_confidence: 0.52, intent_method: "llm", retrieval_score: 0.19, short_circuited: true,
      draft_reply: null, citations: [], risk_level: "high", action: "human_required",
      customer_message: "Halo, bagaimana cuaca di Jakarta besok ya?",
      customer_translation_zh: "你好，明天雅加达天气怎么样？",
      tool_results: { tools_skipped: "语料外问题，未调用订单或物流工具" },
    },
    trace: { runs: [
      { node: "mask", latency_ms: 11, ok: true },
      { node: "classify", latency_ms: 883, token_in: 79, token_out: 22, cost_usd: 0.00004, ok: true },
      { node: "retrieve", latency_ms: 39, ok: true, error: "短路：top1 0.19 < 0.45" },
      { node: "risk_gate", latency_ms: 1, ok: true },
    ]},
  },
};
const ORDER = ["logi", "refund", "oos"];
const SAMPLE_TEXT = {
  logi: "Hi, where is my order? Tracking number is SF1234567890, it's been almost a week.",
  refund: "Quiero un reembolso por el artículo roto que recibí, ¿cuánto tardan?",
  oos: "Halo, bagaimana cuaca di Jakarta besok ya?",
};
const byId = Object.fromEntries(ORDER.map((k) => [SAMPLES[k].ticket.ticket_id, SAMPLES[k]]));
const LIVE_TICKET_IDS = [];

/* ---------------- helpers ---------------- */
const $ = (s) => document.querySelector(s);
const esc = (s) => (s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmtCost = (c) => (c == null ? "—" : "$" + c.toFixed(5));
const srcName = (u) => (u || "").replace(/^file:\/\//, "");
let selectedId = null;

/* ---------------- 工单列表 ---------------- */
function renderList() {
  const box = $("#tickets");
  box.innerHTML = "";
  const ids = USE_MOCK ? ORDER.map((k) => SAMPLES[k].ticket.ticket_id) : LIVE_TICKET_IDS;
  ids.forEach((id) => {
    const sample = byId[id];
    if (!sample) return;
    const t = sample.ticket;
    if ($("#status-filter").value && t.status !== $("#status-filter").value) return;
    const row = document.createElement("div");
    row.className = "tk" + (t.ticket_id === selectedId ? " active" : "");
    row.tabIndex = 0;
    row.innerHTML =
      `<span class="tk-dot ${t.risk_level}"></span>` +
      `<div class="tk-main">` +
        `<div class="tk-top"><span class="tk-id">#${t.ticket_id}</span>` +
        `<span class="tk-meta">${t.intent} · ${t.lang}</span></div>` +
        `<div class="tk-preview">${esc(t.customer_message || "当前工单未返回客户消息")}</div>` +
        `<div class="tk-state ${t.status === "awaiting_review" ? "todo" : ""}">${esc(statusLabel(t.status))}</div>` +
      `</div>` +
      `<span class="badge ${t.risk_level}">${t.risk_level}</span>`;
    row.onclick = () => selectTicket(t.ticket_id);
    row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTicket(t.ticket_id); } };
    box.appendChild(row);
  });
  $("#tk-count").textContent = box.children.length + " 条";
}

function upsertTicket(sample, { select = false, realtime = false } = {}) {
  const id = sample.ticket.ticket_id;
  byId[id] = sample;
  if (realtime && !LIVE_TICKET_IDS.includes(id)) LIVE_TICKET_IDS.unshift(id);
  if (select) selectedId = id;
  renderList();
  if (select) renderDetail(sample);
}

async function selectTicket(id) {
  selectedId = id;
  renderList();
  renderDetail(byId[id]);
  if (!USE_MOCK) {
    try {
      const [ticket, trace] = await Promise.all([getTicket(id), getTrace(id)]);
      byId[id] = { ticket, trace };
      if (selectedId === id) renderDetail(byId[id]);
    } catch (error) { showToast(`读取工单失败：${error.message}`, "error"); }
  }
}

/* ---------------- 详情：围绕「5 秒内做决定」排序 ----------------
   裁决通道 → 草稿+依据 → 决策动作 →（折叠）管线轨迹。
   节点耗时对决策最不重要，故降级为可折叠的次要信息。 */
function renderDetail(sample) {
  const t = sample.ticket, trace = sample.trace;
  const el = $("#detail");
  el.innerHTML =
    detailHead(t) +
    workHtml(t) +
    traceFoldHtml(trace) +
    `<details class="audit-history"><summary>审核记录与最终回复</summary><button class="text-action" data-history>加载审核记录</button><div id="review-history"></div></details>`;
  renderTrace(t, trace);
  el.querySelector("[data-history]").addEventListener("click", (event) => loadHistory(t.ticket_id, event.currentTarget));
  el.querySelectorAll("[data-retry]").forEach((button) => {
    button.addEventListener("click", () => retryTicket(t, button));
  });
  el.querySelectorAll("[data-review]").forEach((button) => {
    button.addEventListener("click", () => submitReview(button.dataset.review, t));
  });
  el.querySelectorAll("[data-translate]").forEach((button) => {
    button.addEventListener("click", () => requestTranslation(button.dataset.translate, t, button));
  });
}

function statusLabel(status) {
  return ({ processing: "处理中", retrying: "重试中", awaiting_review: "待审核", ready_to_send: "待发送",
    sent: "已发送", closed: "历史已关闭", failed: "处理失败", refused: "需人工接手", deferred: "预算不足，已延期" })[status] || status || "未知状态";
}

function detailHead(t) {
  const status = statusLabel(t.status);
  return `<div class="detail-head">` +
    `<div><div class="detail-kicker">当前工单</div><span class="d-id">#${t.ticket_id}</span></div>` +
    `<span class="badge ${t.risk_level}">${({high:"高风险",mid:"中风险",low:"低风险"})[t.risk_level] || "风险待评估"}</span>` +
    `<span class="d-status">${esc(status)}</span>` +
  `</div>`;
}

function traceFoldHtml(trace) {
  const runs = (trace && trace.runs) || [];
  const total = runs.reduce((a, r) => a + (r.latency_ms || 0), 0);
  const cost = runs.reduce((a, r) => a + (r.cost_usd || 0), 0);
  return `<details class="trace-fold"><summary>查看系统运行记录` +
    `<span class="sum-num">${runs.length} 个节点 · ${total} ms · ${fmtCost(cost)}</span></summary>` +
    `<div class="trace" id="trace"></div></details>`;
}

function renderTrace(ticket, trace) {
  const box = $("#trace");
  if (!box) return;
  const runs = (trace && trace.runs) || [];
  const byNode = Object.fromEntries(runs.map((r) => [r.node, r]));
  const maxLat = Math.max(1, ...runs.map((r) => r.latency_ms || 0));

  box.innerHTML = NODES.map((node) => {
    const run = byNode[node];
    let state = run ? "ok" : "skip";
    if (run && node === "retrieve" && ticket.short_circuited) state = "block";
    if (run && node === "risk_gate" && ticket.risk_level === "high") state = "block";
    const w = run ? Math.max(4, Math.round((run.latency_ms / maxLat) * 100)) : 0;
    const note = run ? (run.error || "") : "未执行";
    return `<div class="trace-row ${state}">` +
      `<span class="tr-dot ${state}"></span>` +
      `<div class="tr-name">${node}<small>${NODE_CN[node]}</small></div>` +
      `<div class="tr-track">${run ? `<div class="tr-fill" style="width:${w}%"></div>` : ""}</div>` +
      `<div class="tr-dur">${run ? run.latency_ms + " ms" : "—"}</div>` +
      (note ? `<div class="tr-note">${esc(note)}</div>` : "") +
    `</div>`;
  }).join("");
}

/* 签名元素：裁决通道带 —— 像海关的绿色/红色通道，动词是全页最大的字 */
function channelHtml(t) {
  const lvl = VERDICT[t.risk_level] ? t.risk_level : "high";
  const v = VERDICT[lvl];
  const why = lvl === "high" && t.short_circuited
    ? "没有可依据的答案 —— 检索分数低于阈值，Agent 不会猜。"
    : lvl === "high" ? "此工单需要人工核对，请结合客户消息与依据判断。"
    : lvl === "mid" ? "风险不高，但发送前值得看一眼。"
    : "回复草稿已生成，请确认后续发送状态。";
  const actions = t.status === "awaiting_review" && t.draft_reply
    ? `<div class="decision-actions">` +
        `<button class="btn btn-pass" data-review="approved">确认待发送</button>` +
        `<button class="btn btn-ghost" data-review="edited">修改回复</button>` +
        `<button class="text-action" data-review="rejected">退回重办</button>` +
        `<div class="review-help">先核对退款金额与到账时效；确认无误后点击“确认待发送”。</div>` +
      `</div>`
    : (["failed", "deferred"].includes(t.status)
      ? `<div class="decision-actions"><button class="btn btn-primary" data-retry>重新处理工单</button><div class="review-help">重新生成回复会再次调用模型。</div></div>` : "");
  return `<div class="channel ${lvl}">` +
    `<div class="ch-edge" aria-hidden="true"></div>` +
    `<div class="ch-main"><div class="ch-kicker">Agent 建议</div><div class="ch-verb">${v.tag}</div><div class="ch-why">${why}</div>${actions}</div>` +
  `</div>`;
}

/* 收件箱工作区：中间是客户会话，右侧是 Agent 助手。 */
const THRESHOLD = 0.45;

function workHtml(t) {
  const langName = LANG_CN[t.lang] || t.lang || "—";
  const held = t.action !== "auto_send";

  let draft;
  if (t.draft_reply) {
    draft =
      `<div class="draft ${held ? "held" : ""}">` +
        `<header><span class="label">建议回复</span><span class="r-lang">${langName}</span></header>` +
        `<div class="r-body">${esc(t.draft_reply)}</div>` +
        translationHtml("draft", t) +
      `</div>`;
  } else {
    draft =
      `<div class="draft held"><header><span class="label">未生成草稿</span></header>` +
      `<div class="r-none">${t.status === "processing" || t.status === "retrying" ? "工单正在处理中，请稍后刷新。" : t.short_circuited ? "没有找到足够依据，请人工接手。" : t.status === "failed" ? "处理未完成，可尝试重新处理工单。" : t.status === "deferred" ? "工单因预算限制延期，请检查预算后重试。" : "当前没有可展示的回复，请查看工单状态。"}</div></div>`;
  }

  // 依据来源：无引用是警告态，不是中性信息
  const cites = (t.citations || []).length
    ? t.citations.map((c) => `<span class="cite">${esc(srcName(c))}</span>`).join("")
    : `<span class="cite none">无引用 · 已标记</span>`;

  // 检索刻度：把「短路阈值」这个核心机制画出来
  const rs = t.retrieval_score;
  let gauge = `<div class="verdict-note">本工单未进入检索环节。</div>`;
  if (rs != null) {
    const pct = Math.max(2, Math.min(100, Math.round(rs * 100)));
    const under = rs < THRESHOLD;
    gauge =
      `<div class="gauge ${under ? "under" : ""}">` +
        `<div class="fill" style="width:${pct}%"></div>` +
        `<div class="mark" style="left:${THRESHOLD * 100}%" title="短路阈值 ${THRESHOLD}"></div>` +
      `</div>` +
      `<div class="gauge-legend"><span>top-1 <b>${rs.toFixed(2)}</b></span><span>阈值 ${THRESHOLD}</span></div>` +
      `<div class="verdict-note">${under
        ? "低于阈值 → 短路拒答，不进生成。"
        : "高于阈值 → 有依据，可进入生成。"}</div>`;
  }

  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  const conf = t.intent_confidence != null ? t.intent_confidence.toFixed(2) : "—";

  const evidence =
    `<details class="evidence-fold">` +
      `<summary>为什么这样处理？<span>查看 AI 判断依据</span></summary>` +
      `<div class="evidence">` +
      toolHtml(t) +
      `<div class="ev"><h4>依据来源</h4><div class="cites">${cites}</div></div>` +
      `<div class="ev"><h4>检索</h4>${gauge}</div>` +
      `<div class="ev"><h4>判定</h4>` +
        kv("语种", t.lang || "—") +
        kv("意图", `${t.intent || "—"} · ${t.intent_method || "—"}`) +
        kv("置信度", conf) +
        kv("风险", (t.risk_level || "—").toUpperCase()) +
      `</div>` +
      `</div>` +
    `</details>`;

  return `<div class="work"><main class="work-main">${conversationHtml(t)}${customerHtml(t)}</main>` +
    `<aside class="agent-assist">${channelHtml(t)}${draft}${evidence}</aside></div>`;
}

function conversationHtml(t) {
  const conversation = t.conversation;
  if (!conversation?.user_id) return "";
  const count = conversation.history_count || 0;
  const recent = (conversation.recent_messages || []).slice(-2);
  return `<section class="conversation-context">` +
    `<header><span class="label">客户上下文</span><span class="customer-note">${esc(conversation.user_id)}</span></header>` +
    `<div class="conversation-copy">AI 已带入此前 ${count} 条同一客户消息，避免脱离上下文回复。</div>` +
    (recent.length ? `<div class="conversation-history">${recent.map((m) => `<div>${esc(m.message || "")}</div>`).join("")}</div>` : "") +
  `</section>`;
}

function customerHtml(t) {
  const message = t.customer_message || "当前工单未返回可展示的客户原话";
  return `<section class="customer-message">` +
    `<header><div class="customer-heading"><span class="customer-avatar">客</span><div><span class="label">客户最新消息</span><span class="customer-note">原文已脱敏</span></div></div><span class="message-time">刚刚</span></header>` +
    `<div class="customer-body"><span class="message-sender">客户</span>${esc(message)}</div>` +
    translationHtml("customer", t) +
  `</section>`;
}

function translationHtml(kind, ticket) {
  if (ticket.lang === "zh") return "";
  return `<div class="translation">` +
    `<button class="translate-btn" data-translate="${kind}">翻译成中文</button>` +
    `<span class="translation-note">${USE_MOCK ? "演示译文" : "调用翻译服务"}</span>` +
    `<div class="translation-result" data-translation-box="${kind}" hidden></div>` +
  `</div>`;
}

async function requestTranslation(kind, ticket, button) {
  const box = button.closest(".translation").querySelector("[data-translation-box]");
  const source = kind === "customer" ? ticket.customer_message : ticket.draft_reply;
  if (!source) return;
  button.disabled = true;
  button.textContent = "翻译中…";
  try {
    let translation;
    if (USE_MOCK) {
      translation = ticket[`${kind}_translation_zh`];
      if (!translation) throw new Error("当前样例未提供译文");
    } else {
      const r = await fetch(`${API.base}/translate`, {
        method: "POST", headers: authh(), body: JSON.stringify({ text: source, target_lang: "zh" }),
      });
      if (!r.ok) throw new Error("翻译服务暂不可用");
      ({ translation } = await r.json());
    }
    box.textContent = translation;
    box.hidden = false;
    button.textContent = "已显示中文译文";
  } catch (e) {
    button.disabled = false;
    button.textContent = "重新翻译";
    showToast(`翻译失败：${e.message || e}`, "error");
  }
}

function toolHtml(t) {
  const result = t.tool_results || {};
  const order = result.order;
  const tracking = result.tracking;
  if (order) {
    const latest = tracking?.events?.[0];
    return `<div class="ev tool-result"><h4>业务工具结果</h4>` +
      `<div class="tool-kv"><span>订单</span><b>${esc(order.order_no || "—")}</b></div>` +
      `<div class="tool-kv"><span>商品</span><b>${esc(order.product_name || "—")}</b></div>` +
      `<div class="tool-kv"><span>状态</span><b>${esc(order.status || "—")}</b></div>` +
      (order.tracking_no ? `<div class="tool-kv"><span>运单</span><b>${esc(order.tracking_no)}</b></div>` : "") +
      (latest ? `<div class="tool-event"><b>最新轨迹</b><span>${esc(latest.status || "—")} · ${esc(latest.location || "—")}</span></div>` : "") +
    `</div>`;
  }
  return `<div class="ev tool-result"><h4>业务工具结果</h4>` +
    `<div class="tool-empty">${esc(result.tools_skipped || "本工单未命中可展示的工具结果")}</div>` +
  `</div>`;
}

/* ---------------- 运行 ---------------- */
function pickScenario(text) {
  const s = text.toLowerCase();
  if (/reembolso|refund|devol|\$\d|rusak|roto|broken/.test(s)) return "refund";
  if (/cuaca|weather|clima|poem|joke|stock|invest/.test(s)) return "oos";
  return "logi";
}
async function run() {
  const btn = $("#run");
  const text = $("#ticket-input").value.trim();
  if (!text) { $("#ticket-input").focus(); return; }
  btn.disabled = true; btn.textContent = "运行中…";
  try {
    let sample;
    if (USE_MOCK) {
      sample = structuredClone(SAMPLES[pickScenario(text)]);
      sample.ticket.customer_message = text;
      await new Promise((r) => setTimeout(r, 240));
    } else {
      const created = await createTicket(text);
      const ticket = await pollTicket(created.ticket_id);
      sample = { ticket, trace: await getTrace(created.ticket_id) };
    }
    upsertTicket(sample, { select: true, realtime: !USE_MOCK });
    $("#detected").textContent = sample.ticket.lang || "—";
  } catch (e) {
    $("#detail").innerHTML = `<div class="empty"><div class="big">请求失败</div><div>${esc(String(e.message || e))}</div></div>`;
  } finally {
    btn.disabled = false; btn.textContent = "运行 Agent";
  }
}

/* ---------------- 真实 API（USE_MOCK=false 时使用） ---------------- */
const authh = () => ({ Authorization: `Bearer ${API.token}`, "Content-Type": "application/json" });
async function createTicket(text, lang = null) {
  const r = await fetch(`${API.base}/tickets`, { method: "POST", headers: authh(), body: JSON.stringify({ text, lang }) });
  if (!r.ok) throw new Error(`POST /tickets → ${r.status}`);
  return r.json();
}
async function getTicket(id) {
  const r = await fetch(`${API.base}/tickets/${id}`, { headers: authh() });
  if (!r.ok) throw new Error(`GET /tickets/${id} → ${r.status}`);
  return r.json();
}
async function getTrace(id) {
  const r = await fetch(`${API.base}/tickets/${id}/trace`, { headers: authh() });
  if (!r.ok) throw new Error(`GET trace → ${r.status}`);
  return r.json();
}
async function pollTicket(id, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const t = await getTicket(id);
    if (!["processing", "retrying"].includes(t.status)) return t;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("等待处理超时");
}

const pendingReviews = new Set();
async function submitReview(action, ticket) {
  let finalReply = null;
  if (action === "edited") {
    finalReply = window.prompt("修改待发送回复：", ticket.draft_reply || "");
    if (finalReply === null) return;
    if (!finalReply.trim()) { showToast("修改回复不能为空", "error"); return; }
  }
  if (pendingReviews.has(ticket.ticket_id)) return;
  pendingReviews.add(ticket.ticket_id);
  try {
    let updated;
    if (USE_MOCK) {
      updated = structuredClone(ticket);
      updated.status = action === "rejected" ? "failed" : "ready_to_send";
      if (finalReply) updated.draft_reply = finalReply;
    } else {
      const r = await fetch(`${API.base}/review/${ticket.ticket_id}`, {
        method: "POST", headers: authh(), body: JSON.stringify({ action, final_reply: finalReply }),
      });
      if (!r.ok) throw new Error(`POST /review/${ticket.ticket_id} → ${r.status}`);
      updated = await getTicket(ticket.ticket_id);
    }
    updated.review_note = action === "rejected" ? "已退回重办（演示）" : "已通过，等待外发（演示）";
    byId[updated.ticket_id] = { ticket: updated, trace: byId[updated.ticket_id]?.trace || { runs: [] } };
    selectedId = updated.ticket_id;
    renderList();
    renderDetail(byId[updated.ticket_id]);
    showToast(action === "rejected" ? "工单已退回重办；未向客户外发消息" : "审核状态已更新；未向客户外发消息", action === "rejected" ? "warn" : "success");
  } catch (e) {
    showToast(`审核操作失败：${e.message || e}`, "error");
  } finally { pendingReviews.delete(ticket.ticket_id); }
}

function showToast(message, kind = "success") {
  let toast = $("#toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "toast";
    toast.setAttribute("role", "status");
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.className = `toast ${kind} show`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 3500);
}

function setFeed(state, label) {
  $("#feed-dot").className = `dot ${state}`;
  $("#feed-label").textContent = label;
}

async function initFeed() {
  if (USE_MOCK) {
    setFeed("sample", "样例模式");
    return;
  }
  try {
    const r = await fetch(`${API.base}/health`, { cache: "no-store" });
    if (!r.ok) throw new Error("health check failed");
    const health = await r.json();
    loadTickets(true);
    if (health.mode === "live_demo") {
      $("#operations").textContent = "实时演示使用内存工单；运行统计请连接主服务。";
      initRealtime();
    } else { setFeed("live", "后台已连接"); refreshOperations(); }
  } catch (_) {
    setFeed("down", "后端不可用");
  }
}

function initRealtime() {
  if (USE_MOCK || !window.EventSource) return;
  if (initRealtime.stream) initRealtime.stream.close();
  const stream = new EventSource(`${API.base}/events?token=${encodeURIComponent(API.token)}`);
  initRealtime.stream = stream;
  stream.addEventListener("open", () => setFeed("live", "实时会话已连接"));
  stream.addEventListener("agent_suggestion", (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (!payload.ticket || !payload.trace) return;
      upsertTicket({ ticket: payload.ticket, trace: payload.trace }, { select: true, realtime: true });
      showToast(`收到 ${payload.user_id} 的新消息，Agent 已给出建议`, "success");
    } catch (_) {
      showToast("实时消息解析失败", "error");
    }
  });
  stream.addEventListener("error", () => setFeed("down", "实时通道重连中"));
}

/* ---------------- 事件绑定 ---------------- */
$("#samples").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $("#ticket-input").value = SAMPLE_TEXT[chip.dataset.sc] || "";
  $("#detected").textContent = "—";
  $("#ticket-input").focus();
});
$("#run").addEventListener("click", run);
$("#ticket-input").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run();
});
/* ---------------- 初始 ---------------- */
renderList();
if (USE_MOCK) selectTicket(SAMPLES.refund.ticket.ticket_id);
initFeed();


let nextCursor = 0;
let queueRequest = 0;
async function loadTickets(reset = false) {
  if (USE_MOCK) { renderList(); return; }
  const request = ++queueRequest;
  $("#queue-message").textContent = "正在加载…";
  $("#load-more").disabled = true;
  try {
    const params = new URLSearchParams({ limit: "25", after_id: reset ? "0" : String(nextCursor) });
    if ($("#status-filter").value) params.set("status", $("#status-filter").value);
    const page = await apiJson(`/tickets?${params}`);
    if (request !== queueRequest) return;
    if (reset) LIVE_TICKET_IDS.splice(0);
    for (const ticket of page.items) {
      byId[ticket.ticket_id] = { ticket, trace: byId[ticket.ticket_id]?.trace || { runs: [] } };
      if (!LIVE_TICKET_IDS.includes(ticket.ticket_id)) LIVE_TICKET_IDS.push(ticket.ticket_id);
    }
    nextCursor = page.next_cursor;
    $("#load-more").hidden = nextCursor === null;
    $("#queue-message").textContent = LIVE_TICKET_IDS.length ? "" : "当前筛选下没有工单";
    renderList();
    if (reset) {
      if (LIVE_TICKET_IDS.length) selectTicket(LIVE_TICKET_IDS[0]);
      else { selectedId = null; $("#detail").textContent = "请选择其他工单状态，或接收一条新消息。"; }
    }
  } catch (error) {
    if (request === queueRequest) $("#queue-message").textContent = `加载失败：${error.message}`;
  } finally { if (request === queueRequest) $("#load-more").disabled = false; }
}

async function apiJson(path, options = {}) {
  const response = await fetch(`${API.base}${path}`, { ...options, headers: authh() });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `请求失败（${response.status}）`);
  return data;
}

async function retryTicket(ticket, button) {
  button.disabled = true;
  try {
    if (USE_MOCK) { showToast("样例工单不能重新调用模型，请连接真实服务。", "warn"); return; }
    await apiJson(`/tickets/${ticket.ticket_id}/retry`, { method: "POST" });
    const updated = await getTicket(ticket.ticket_id);
    upsertTicket({ ticket: updated, trace: { runs: [] } }, { select: true, realtime: true });
    showToast("工单已重新入队，可刷新查看进度。");
    refreshOperations();
  } catch (error) { showToast(`重试失败：${error.message}`, "error"); }
  finally { button.disabled = false; }
}

async function loadHistory(id, button) {
  button.disabled = true;
  const target = $("#review-history");
  try {
    if (USE_MOCK) { target.textContent = "样例工单没有数据库审核记录。"; return; }
    const page = await apiJson(`/review/${id}/history`);
    if (selectedId !== id) return;
    target.replaceChildren();
    if (!page.items.length) target.textContent = "暂无审核记录";
    for (const item of page.items) {
      const row = document.createElement("div");
      row.className = "audit-entry";
      row.textContent = `${item.created_at || ""} · ${{approved:"通过",edited:"修改通过",rejected:"拒绝"}[item.action] || item.action}\n原草稿：${item.draft_reply || "—"}\n最终回复：${item.final_reply || "—"}`;
      target.appendChild(row);
    }
    if (page.next_cursor !== null) target.append("当前显示前 50 条审核记录。");
  } catch (error) { if (selectedId === id) target.textContent = `读取失败：${error.message}`; }
  finally { button.disabled = false; }
}

async function refreshOperations() {
  if (USE_MOCK) return;
  const target = $("#operations");
  try {
    const data = await apiJson("/metrics/operations");
    const format = value => value == null ? "暂无数据" : `${Math.round(value)} ms`;
    target.replaceChildren();
    const values = document.createElement("div");
    values.className = "ops-values";
    values.textContent = `待审核 ${data.by_status.awaiting_review || 0} · 失败 ${data.by_status.failed || 0} · 延期 ${data.by_status.deferred || 0} · 节点累计耗时 P50 ${format(data.agent_processing_ms.p50)} / P95 ${format(data.agent_processing_ms.p95)} · 已记录估算费用 $${data.estimated_cost_usd.toFixed(5)}`;
    target.appendChild(values);
    for (const alert of data.alerts) {
      const row = document.createElement("div"); row.className = "ops-alert";
      row.textContent = `${alert.message}（${alert.count} 条）`; target.appendChild(row);
    }
    const note = document.createElement("small");
    note.textContent = `仅统计真实工单；耗时为各节点累计执行时间，包含重试，不含排队和人工等待。费用是模型配置价估算，样本 ${data.cost_sample_count} 张工单。`;
    target.appendChild(note);
  } catch (error) { target.textContent = `运行指标不可用：${error.message}`; }
}

$("#status-filter").addEventListener("change", () => loadTickets(true));
$("#refresh-tickets").addEventListener("click", () => loadTickets(true));
$("#load-more").addEventListener("click", () => loadTickets(false));
$("#refresh-operations").addEventListener("click", refreshOperations);


$("#connect-api").addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const base = new URL($("#api-base").value.trim());
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error("请输入有效的 HTTP API 地址");
    if (!$("#api-token").value.trim()) throw new Error("访问令牌不能为空");
    if (initRealtime.stream) initRealtime.stream.close();
    API.base = base.href.replace(/\/$/, "");
    API.token = $("#api-token").value.trim();
    $("#api-token").value = "";
    USE_MOCK = false;
    LIVE_TICKET_IDS.splice(0); renderList();
    $("#detail").textContent = "正在连接后台…";
    initFeed();
  } catch (error) { showToast(error.message, "error"); }
});


async function inspectAmazon(actions = false) {
  const target = $("#amazon-result");
  if (USE_MOCK) { target.textContent = "请先连接主服务，并在服务端配置 Amazon 卖家授权。"; return; }
  const id = $("#amazon-order-id").value.trim();
  if (!/^[0-9]{3}-[0-9]{7}-[0-9]{7}$/.test(id)) { target.textContent = "请输入 3-7-7 位格式的 Amazon 订单号。"; return; }
  const buttons = $("#amazon-lookup").querySelectorAll("button");
  buttons.forEach(button => button.disabled = true);
  target.textContent = "正在查询 Amazon…";
  try {
    const data = await apiJson(`/integrations/amazon/orders/${encodeURIComponent(id)}${actions ? "/message-actions" : ""}`);
    target.textContent = actions
      ? `可用消息类型：${data.available_message_types.join("、") || "暂无"}。本页面仅查询，不发送消息。`
      : `${data.sandbox ? "沙箱样例" : "Amazon"} · 订单 ${data.order_id} · 履约状态 ${data.fulfillment_status || "未知"} · 履约方 ${data.fulfilled_by || "未知"}`;
  } catch (error) { target.textContent = `查询失败：${error.message}`; }
  finally { buttons.forEach(button => button.disabled = false); }
}
$("#amazon-lookup").addEventListener("submit", event => { event.preventDefault(); inspectAmazon(false); });
$("#amazon-actions").addEventListener("click", () => inspectAmazon(true));
