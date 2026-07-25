/* 跨境客服 · Agent 控制台 —— 演示前端（干净中性仪表盘版）
 *
 * 默认走 SAMPLE 数据（结构与后端 API 完全一致），无需 LLM key / worker 即可演示。
 * USE_MOCK 改 false 即切真实后端：createTicket → 轮询 getTicket → getTrace，渲染函数不变。
 * 文案用中文；后端真实字段值（节点名 / action / 语种码 / rule·llm）保留英文等宽。
 */
const USE_MOCK = false;
const API = { base: "http://127.0.0.1:8000", token: "dev-token" };
const NODES = ["mask", "classify", "retrieve", "tools", "generate", "risk_gate"];
const NODE_CN = { mask: "脱敏", classify: "分类", retrieve: "检索", tools: "工具", generate: "生成", risk_gate: "风险闸门" };
const VERDICT = {
  low:  { head: "可自动发送",     action: "auto_send",      tag: "放行" },
  mid:  { head: "需人工快速确认", action: "quick_review",   tag: "复核" },
  high: { head: "转人工处理",     action: "human_required", tag: "扣留" },
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
  ORDER.forEach((k) => {
    const t = SAMPLES[k].ticket;
    const row = document.createElement("div");
    row.className = "tk" + (t.ticket_id === selectedId ? " active" : "");
    row.tabIndex = 0;
    row.innerHTML =
      `<span class="tk-dot ${t.risk_level}"></span>` +
      `<div class="tk-main">` +
        `<div class="tk-top"><span class="tk-id">#${t.ticket_id}</span>` +
        `<span class="tk-meta">${t.intent} · ${t.lang}</span></div>` +
        `<div class="tk-preview">${esc(SAMPLE_TEXT[k])}</div>` +
      `</div>` +
      `<span class="badge ${t.risk_level}">${t.risk_level}</span>`;
    row.onclick = () => selectTicket(t.ticket_id);
    row.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectTicket(t.ticket_id); } };
    box.appendChild(row);
  });
  $("#tk-count").textContent = ORDER.length + " 条";
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
    channelHtml(t) +
    workHtml(t) +
    traceFoldHtml(trace);
  renderTrace(t, trace);
}

function detailHead(t) {
  return `<div class="detail-head">` +
    `<span class="d-id">#${t.ticket_id}</span>` +
    `<span class="badge ${t.risk_level}">${t.risk_level}</span>` +
    `<span class="d-meta">${t.intent} · ${t.intent_method} · ${t.lang}</span>` +
    `<span class="d-status">${t.status}</span>` +
  `</div>`;
}

function traceFoldHtml(trace) {
  const runs = (trace && trace.runs) || [];
  const total = runs.reduce((a, r) => a + (r.latency_ms || 0), 0);
  const cost = runs.reduce((a, r) => a + (r.cost_usd || 0), 0);
  return `<details class="trace-fold"><summary>管线轨迹` +
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
  return `<div class="channel ${lvl}">` +
    `<div class="ch-edge" aria-hidden="true"></div>` +
    `<div class="ch-main"><div class="ch-verb">${v.tag}</div><div class="ch-why">${why}</div></div>` +
    `<div class="ch-act"><div class="k">动作</div><div class="v">${v.action}</div></div>` +
  `</div>`;
}

/* 工作区：左＝要判断的东西（草稿+决策按钮），右＝判断依据 */
const THRESHOLD = 0.45;

function workHtml(t) {
  const langName = LANG_CN[t.lang] || t.lang || "—";
  const held = t.action !== "auto_send";

  let draft;
  if (t.draft_reply) {
    draft =
      `<div class="draft ${held ? "held" : ""}">` +
        `<header><span class="label">草稿回复</span><span class="r-lang">${langName}</span></header>` +
        `<div class="r-body">${esc(t.draft_reply)}</div>` +
        (t.status === "awaiting_review"
          ? `<div class="decide">` +
              `<button class="btn btn-pass btn-sm">通过并发送</button>` +
              `<button class="btn btn-ghost btn-sm">修改后发送</button>` +
              `<button class="btn btn-ghost btn-sm">退回重办</button>` +
              `<span class="hint">本系统只起草，不实际外发</span>` +
            `</div>`
          : "") +
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
    `<div class="evidence">` +
      `<div class="ev"><h4>依据来源</h4><div class="cites">${cites}</div></div>` +
      `<div class="ev"><h4>检索</h4>${gauge}</div>` +
      `<div class="ev"><h4>判定</h4>` +
        kv("语种", t.lang || "—") +
        kv("意图", `${t.intent || "—"} · ${t.intent_method || "—"}`) +
        kv("置信度", conf) +
        kv("风险", (t.risk_level || "—").toUpperCase()) +
      `</div>` +
    `</div>`;

  return `<div class="work"><div>${draft}</div>${evidence}</div>`;
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
      sample = SAMPLES[pickScenario(text)];
      await new Promise((r) => setTimeout(r, 240));
    } else {
      const created = await createTicket(text);
      const ticket = await pollTicket(created.ticket_id);
      sample = { ticket, trace: await getTrace(created.ticket_id) };
      byId[ticket.ticket_id] = sample;
    }
    $("#detected").textContent = sample.ticket.lang || "—";
    selectTicket(sample.ticket.ticket_id);
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
if (!USE_MOCK) { $("#feed-dot").className = "dot live"; $("#feed-label").textContent = "实时后端"; }

/* ---------------- 初始 ---------------- */
renderList();
selectTicket(SAMPLES.refund.ticket.ticket_id); // 默认展示 HELD 场景，最能体现护栏
