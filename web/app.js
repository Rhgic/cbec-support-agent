/* 跨境客服 · Agent 控制台 —— 演示前端（干净中性仪表盘版）
 *
 * 默认走 SAMPLE 数据（结构与后端 API 完全一致），无需 LLM key / worker 即可演示。
 * USE_MOCK 改 false 即切真实后端：createTicket → 轮询 getTicket → getTrace，渲染函数不变。
 * 文案用中文；后端真实字段值（节点名 / action / 语种码 / rule·llm）保留英文等宽。
 */
// 静态页面默认明确使用样例模式；scripts/demo_server.py 同源伺服时会替换为 false。
const USE_MOCK = true;
const API = { base: window.location.origin, token: "dev-token" };
const NODES = ["mask", "classify", "retrieve", "tools", "generate", "risk_gate"];
const NODE_CN = { mask: "脱敏", classify: "分类", retrieve: "检索", tools: "工具", generate: "生成", risk_gate: "风险闸门" };
const VERDICT = {
  low:  { head: "已自动处理", action: "auto_send",      tag: "已自动回复" },
  mid:  { head: "等待快速确认", action: "quick_review",   tag: "需要确认" },
  high: { head: "等待你的审核", action: "human_required", tag: "需要审核" },
};
const LANG_CN = { en: "英语", es: "西语", id: "印尼语" };

/* ---------------- SAMPLE 数据（== TicketOut + trace） ---------------- */
const SAMPLES = {
  logi: {
    ticket: {
      ticket_id: 4817, status: "closed", lang: "en", intent: "logistics",
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
  const ids = [...LIVE_TICKET_IDS, ...ORDER.map((k) => SAMPLES[k].ticket.ticket_id)];
  ids.forEach((id) => {
    const sample = byId[id];
    if (!sample) return;
    const t = sample.ticket;
    const row = document.createElement("div");
    row.className = "tk" + (t.ticket_id === selectedId ? " active" : "");
    row.tabIndex = 0;
    row.innerHTML =
      `<span class="tk-dot ${t.risk_level}"></span>` +
      `<div class="tk-main">` +
        `<div class="tk-top"><span class="tk-id">#${t.ticket_id}</span>` +
        `<span class="tk-meta">${t.intent} · ${t.lang}</span></div>` +
        `<div class="tk-preview">${esc(t.customer_message || "当前工单未返回客户消息")}</div>` +
        `<div class="tk-state ${t.status === "awaiting_review" ? "todo" : ""}">${t.status === "awaiting_review" ? "需要你审核" : "已处理，无需操作"}</div>` +
      `</div>` +
      `<span class="badge ${t.risk_level}">${t.risk_level}</span>`;
    row.onclick = () => selectTicket(t.ticket_id);
    row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTicket(t.ticket_id); } };
    box.appendChild(row);
  });
  $("#tk-count").textContent = ids.length + " 条";
}

function upsertTicket(sample, { select = false, realtime = false } = {}) {
  const id = sample.ticket.ticket_id;
  byId[id] = sample;
  if (realtime && !LIVE_TICKET_IDS.includes(id)) LIVE_TICKET_IDS.unshift(id);
  if (select) selectedId = id;
  renderList();
  if (select) renderDetail(sample);
}

function selectTicket(id) {
  selectedId = id;
  renderList();
  renderDetail(byId[id]);
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
    traceFoldHtml(trace);
  renderTrace(t, trace);
  el.querySelectorAll("[data-review]").forEach((button) => {
    button.addEventListener("click", () => submitReview(button.dataset.review, t));
  });
  el.querySelectorAll("[data-translate]").forEach((button) => {
    button.addEventListener("click", () => requestTranslation(button.dataset.translate, t, button));
  });
}

function detailHead(t) {
  const status = t.status === "awaiting_review" ? "待处理" : "已完成";
  return `<div class="detail-head">` +
    `<div><div class="detail-kicker">当前工单</div><span class="d-id">#${t.ticket_id}</span></div>` +
    `<span class="badge ${t.risk_level}">${t.risk_level === "high" ? "需审核" : "已处理"}</span>` +
    `<span class="d-status">${status}</span>` +
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
  const lvl = t.risk_level || "high";
  const v = VERDICT[lvl];
  const why = lvl === "high" && t.short_circuited
    ? "没有可依据的答案 —— 检索分数低于阈值，Agent 不会猜。"
    : lvl === "high" ? "回复涉及金额或承诺 —— 需人工确认后才能发出。"
    : lvl === "mid" ? "风险不高，但发送前值得看一眼。"
    : "事实性、有依据、无承诺 —— 可安全自动发送。";
  const actions = t.status === "awaiting_review" && t.draft_reply
    ? `<div class="decision-actions">` +
        `<button class="btn btn-pass" data-review="approved">确认并发送</button>` +
        `<button class="btn btn-ghost" data-review="edited">修改回复</button>` +
        `<button class="text-action" data-review="rejected">退回重办</button>` +
        `<div class="review-help">先核对退款金额与到账时效；确认无误后点击“确认并发送”。</div>` +
      `</div>`
    : "";
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
      `<div class="r-none">Agent 在写回复前就停下了 —— 检索没有找到可依据的内容，于是把工单转给人工，而不是编造。</div></div>`;
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
  const r = await fetch(`${API.base}/tickets/${id}`);
  if (!r.ok) throw new Error(`GET /tickets/${id} → ${r.status}`);
  return r.json();
}
async function getTrace(id) {
  const r = await fetch(`${API.base}/tickets/${id}/trace`);
  if (!r.ok) throw new Error(`GET trace → ${r.status}`);
  return r.json();
}
async function pollTicket(id, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const t = await getTicket(id);
    if (t.status !== "processing") return t;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("等待处理超时");
}

async function submitReview(action, ticket) {
  let finalReply = null;
  if (action === "edited") {
    finalReply = window.prompt("修改草稿后发送（仅演示，不会外发）：", ticket.draft_reply || "");
    if (finalReply === null) return;
  }
  try {
    let updated;
    if (USE_MOCK) {
      updated = structuredClone(ticket);
      updated.status = action === "rejected" ? "failed" : "closed";
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
  }
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
    initRealtime();
  } catch (_) {
    setFeed("down", "后端不可用");
  }
}

function initRealtime() {
  if (USE_MOCK || !window.EventSource) return;
  const stream = new EventSource(`${API.base}/events?token=${encodeURIComponent(API.token)}`);
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
selectTicket(SAMPLES.refund.ticket.ticket_id); // 首屏明确展示唯一需要人工处理的工单
initFeed();
