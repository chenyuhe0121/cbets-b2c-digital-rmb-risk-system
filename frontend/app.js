const $ = (id) => document.getElementById(id);
const state = { orderId: null, adminKey: sessionStorage.getItem("adminKey") || "" };

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (cents) => (Number(cents || 0) / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 4200);
}

async function api(path, { method = "GET", body = null, admin = false } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (admin && state.adminKey) headers["X-Admin-Key"] = state.adminKey;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data.error && data.error.message) || data.detail || res.statusText;
    const err = new Error(msg);
    err.payload = data;
    throw err;
  }
  return data;
}

function show(el, data) { el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2); }

const STATUS_LABEL = {
  created: "待支付", risk_blocked: "风控拦截", paid_escrowed: "已支付·担保中", shipped: "已发货",
  received: "已收货", after_sales: "售后中", arbitrating: "仲裁中", completed: "已完结",
  refunded: "已退款", partially_refunded: "部分退款", cancelled: "已取消",
};
function statusTag(status) {
  const cls = status === "completed" ? "ok"
    : status === "risk_blocked" || status === "refunded" ? "bad"
    : status === "after_sales" || status === "arbitrating" || status === "partially_refunded" ? "warn"
    : "neutral";
  return `<span class="tag ${cls}">${esc(STATUS_LABEL[status] || status)}</span>`;
}

document.querySelectorAll("#nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    $("view-" + btn.dataset.view).classList.add("active");
    if (btn.dataset.view === "orders") loadOrders();
    if (btn.dataset.view === "risk") { loadRules(); loadModels(); }
    if (btn.dataset.view === "audit") loadAnchors();
    if (btn.dataset.view === "metrics") loadMetrics();
  });
});

$("adminState").addEventListener("click", () => {
  const key = prompt("输入管理员密钥（服务端 CBETS_ADMIN_KEY），留空表示清除", state.adminKey);
  if (key === null) return;
  state.adminKey = key.trim();
  sessionStorage.setItem("adminKey", state.adminKey);
  renderAdminState();
});
function renderAdminState() {
  $("adminState").textContent = state.adminKey ? "管理员密钥：已设置" : "管理员密钥：未设置";
}

async function loadHealth() {
  try {
    const h = await api("/healthz");
    $("healthPill").textContent = `服务正常 · ${h.adapter_mode} 适配器`;
    $("healthPill").classList.add("ok");
  } catch {
    $("healthPill").textContent = "服务不可用";
  }
}

async function loadParties() {
  const data = await api("/api/admin/parties", { admin: true });
  const buyers = data.items.filter((p) => p.role === "buyer");
  const sellers = data.items.filter((p) => p.role === "seller");
  const opt = (p) => `<option value="${esc(p.party_id)}">${esc(p.party_id)} · ${esc(p.name)}</option>`;
  $("buyerSel").innerHTML = buyers.map(opt).join("");
  $("sellerSel").innerHTML = sellers.map(opt).join("");
  $("escrowSellerSel").innerHTML = sellers.map(opt).join("");
}

function amountCents() {
  const v = parseFloat($("amountInput").value || "0");
  return Math.round(v * 100);
}

$("btnCreate").addEventListener("click", async () => {
  try {
    const body = {
      buyer_id: $("buyerSel").value,
      seller_id: $("sellerSel").value,
      amount_cents: amountCents(),
      sku_desc: $("skuInput").value,
    };
    const oid = $("orderIdInput").value.trim();
    if (oid) body.order_id = oid;
    const created = await api("/api/orders", { method: "POST", body });
    state.orderId = created.order.order_id;
    $("actionOrderId").value = state.orderId;
    $("disputeOrderId").value = state.orderId;
    show($("tradeOut"), created);
    toast("订单已创建 " + state.orderId);
    loadOrders();
  } catch (e) { toast(e.message, true); show($("tradeOut"), e.payload || e.message); }
});

$("btnPreview").addEventListener("click", async () => {
  const oid = $("actionOrderId").value.trim() || state.orderId;
  if (!oid) return toast("先创建订单", true);
  try {
    const r = await api("/api/risk/preview?order_id=" + encodeURIComponent(oid));
    show($("tradeOut"), r);
    toast(`事前评分 ${r.final_score} · ${r.decision}`);
  } catch (e) { toast(e.message, true); show($("tradeOut"), e.payload || e.message); }
});

$("btnPay").addEventListener("click", async () => {
  const oid = $("actionOrderId").value.trim() || state.orderId;
  if (!oid) return toast("先创建订单", true);
  try {
    const token = $("tokenInput").value.trim();
    const r = await api(`/api/orders/${encodeURIComponent(oid)}/pay`, {
      method: "POST", body: token ? { token } : {},
    });
    show($("tradeOut"), r);
    toast("支付成功，资金已进入担保子钱包");
    loadOrders();
  } catch (e) { toast(e.message, true); show($("tradeOut"), e.payload || e.message); }
});

$("btnIssueToken").addEventListener("click", async () => {
  try {
    const r = await api("/api/orders/tokens/issue", { method: "POST", body: { buyer_id: $("buyerSel").value } });
    $("tokenInput").value = r.token;
    show($("tradeOut"), r);
    toast("支付标记已签发");
  } catch (e) { toast(e.message, true); show($("tradeOut"), e.payload || e.message); }
});

$("btnTopup").addEventListener("click", async () => {
  try {
    const parties = await api("/api/admin/parties", { admin: true });
    const buyer = parties.items.find((p) => p.party_id === $("buyerSel").value);
    const r = await api("/api/admin/wallets/topup", {
      method: "POST", admin: true,
      body: { wallet_id: buyer.wallet_id, amount_cents: 100000 },
    });
    toast("已充值到 " + buyer.wallet_id + "，余额 " + money(r.balance_cents) + " 元");
  } catch (e) { toast(e.message, true); }
});

async function loadOrders() {
  try {
    const data = await api("/api/orders?limit=50");
    if (!data.items.length) { $("ordersTable").className = "empty"; $("ordersTable").textContent = "暂无订单"; return; }
    $("ordersTable").className = "";
    $("ordersTable").innerHTML = `<table><thead><tr><th>订单号</th><th>买家</th><th>商户</th><th>金额</th>
      <th>状态</th><th>合约</th><th>创建时间</th></tr></thead><tbody>
      ${data.items.map((o) => `<tr data-oid="${esc(o.order_id)}" style="cursor:pointer">
        <td class="mono">${esc(o.order_id)}</td><td>${esc(o.buyer_id)}</td><td>${esc(o.seller_id)}</td>
        <td>${money(o.amount_cents)}</td><td>${statusTag(o.status)}</td>
        <td class="mono">${esc(o.contract_status || "-")}</td>
        <td class="mono">${esc((o.created_at || "").slice(0, 19))}</td></tr>`).join("")}
      </tbody></table>`;
    $("ordersTable").querySelectorAll("tr[data-oid]").forEach((tr) => {
      tr.addEventListener("click", () => openOrder(tr.dataset.oid));
    });
  } catch (e) { toast(e.message, true); }
}

async function openOrder(oid) {
  state.orderId = oid;
  $("actionOrderId").value = oid;
  $("disputeOrderId").value = oid;
  try {
    const d = await api("/api/orders/" + encodeURIComponent(oid));
    show($("orderOut"), d);
  } catch (e) { toast(e.message, true); }
}

$("btnReloadOrders").addEventListener("click", loadOrders);

async function orderAction(path, body) {
  const oid = $("actionOrderId").value.trim();
  if (!oid) return toast("先填订单号", true);
  try {
    const r = await api(`/api/orders/${encodeURIComponent(oid)}/${path}`, { method: "POST", body: body || {} });
    show($("orderOut"), r);
    toast(path + " 完成");
    loadOrders();
  } catch (e) { toast(e.message, true); show($("orderOut"), e.payload || e.message); }
}

$("btnShip").addEventListener("click", () => orderAction("ship", {
  carrier: $("carrierInput").value || "燕文",
  tracking_no: $("trackingInput").value || ("YW" + Date.now()),
}));
$("btnReceive").addEventListener("click", () => orderAction("receive", {}));
$("btnRelease").addEventListener("click", () => orderAction("release", {
  reviewer: $("reviewerInput").value.trim() || null,
}));

async function loadRules() {
  try {
    const data = await api("/api/risk/rules");
    $("rulesTable").className = "";
    $("rulesTable").innerHTML = `<table><thead><tr><th>编号</th><th>范围</th><th>规则</th><th>条件</th>
      <th>动作</th><th>权重</th><th>状态</th><th></th></tr></thead><tbody>
      ${data.items.map((r) => `<tr>
        <td class="mono">${esc(r.rule_id)}</td><td>${r.scope === "buyer" ? "买家" : "商户"}</td>
        <td>${esc(r.name)}</td>
        <td class="mono">${esc(r.field)} ${esc(r.op)} ${esc(JSON.stringify(r.threshold))}</td>
        <td>${r.action === "block" ? '<span class="tag bad">拦截</span>' : '<span class="tag warn">复核</span>'}</td>
        <td>${r.weight}</td>
        <td>${r.enabled ? '<span class="tag ok">启用</span>' : '<span class="tag neutral">停用</span>'}</td>
        <td><button class="btn ghost" style="padding:4px 10px" data-toggle="${esc(r.rule_id)}" data-enabled="${r.enabled}">切换</button></td>
      </tr>`).join("")}</tbody></table>`;
    $("rulesTable").querySelectorAll("[data-toggle]").forEach((b) => {
      b.addEventListener("click", () => toggleRule(b.dataset.toggle, b.dataset.enabled));
    });
  } catch (e) { toast(e.message, true); }
}

async function toggleRule(ruleId, enabled) {
  const next = !Number(enabled);
  try {
    await api("/api/risk/rules/" + ruleId, { method: "PATCH", admin: true, body: { enabled: next } });
    toast(ruleId + (next ? " 已启用" : " 已停用"));
    loadRules();
  } catch (e) { toast(e.message, true); }
}

async function loadModels() {
  try {
    const m = await api("/api/risk/models");
    $("modelInfo").className = "kv";
    $("modelInfo").innerHTML = `
      <dt>买家模型</dt><dd>${esc(m.buyer.version)} · AUC ${esc(m.buyer.metrics.auc_valid)} · 样本 ${esc(m.buyer.metrics.samples)}</dd>
      <dt>商户模型</dt><dd>${esc(m.seller.version)} · AUC ${esc(m.seller.metrics.auc_valid)} · 样本 ${esc(m.seller.metrics.samples)}</dd>
      <dt>买家特征</dt><dd>${m.buyer.feature_names.map(esc).join(" / ")}</dd>
      <dt>商户特征</dt><dd>${m.seller.feature_names.map(esc).join(" / ")}</dd>`;
  } catch (e) { toast(e.message, true); }
}

$("btnReloadRules").addEventListener("click", loadRules);

$("btnLoadEscrow").addEventListener("click", async () => {
  const sid = $("escrowSellerSel").value;
  try {
    const r = await api("/api/escrow/" + encodeURIComponent(sid));
    show($("escrowOut"), r);
    if (!r.reconcile.consistent) toast("注意：担保余额与合约不一致", true);
  } catch (e) { toast(e.message, true); show($("escrowOut"), e.payload || e.message); }
});

$("btnOpenDispute").addEventListener("click", async () => {
  const oid = $("disputeOrderId").value.trim();
  if (!oid) return toast("先填订单号", true);
  let evidence = {};
  try { evidence = JSON.parse($("disputeEvidence").value || "{}"); }
  catch { return toast("证据 JSON 格式不对", true); }
  try {
    const r = await api(`/api/orders/${encodeURIComponent(oid)}/dispute`, {
      method: "POST",
      body: { dtype: $("disputeType").value, claimant: $("disputeClaimant").value, evidence },
    });
    show($("disputeOut"), r);
    if (r.dispute_id) $("arbDisputeId").value = r.dispute_id;
    toast(r.auto ? "已自动裁决" : "已转人工仲裁");
  } catch (e) { toast(e.message, true); show($("disputeOut"), e.payload || e.message); }
});

$("btnArbitrate").addEventListener("click", async () => {
  const did = $("arbDisputeId").value.trim();
  if (!did) return toast("先填纠纷号", true);
  try {
    const r = await api(`/api/disputes/${encodeURIComponent(did)}/arbitrate`, {
      method: "POST", admin: true,
      body: {
        refund_cents: Number($("arbRefund").value || 0),
        release_cents: Number($("arbRelease").value || 0),
        reviewer: $("arbReviewer").value || "仲裁员",
        note: $("arbNote").value,
      },
    });
    show($("arbOut"), r);
    toast("裁决已执行");
  } catch (e) { toast(e.message, true); show($("arbOut"), e.payload || e.message); }
});

$("btnVerify").addEventListener("click", async () => {
  try {
    const r = await api("/api/audit/verify");
    show($("auditOut"), r);
    toast(r.valid ? "哈希链完整" : "哈希链被改动", !r.valid);
  } catch (e) { toast(e.message, true); }
});

$("btnAnchor").addEventListener("click", async () => {
  try {
    const r = await api("/api/audit/anchor", { method: "POST", admin: true });
    show($("auditOut"), r);
    toast(r.result === "nothing_to_anchor" ? "没有新账本需要锚定" : "已锚定 Merkle 根");
    loadAnchors();
  } catch (e) { toast(e.message, true); show($("auditOut"), e.payload || e.message); }
});

async function loadAnchors() {
  try {
    const data = await api("/api/audit/anchors");
    if (!data.items.length) { $("anchorsTable").className = "empty"; $("anchorsTable").textContent = "尚未锚定"; return; }
    $("anchorsTable").className = "";
    $("anchorsTable").innerHTML = `<table><thead><tr><th>批次</th><th>序号区间</th><th>Merkle 根</th>
      <th>链</th><th>tx</th><th>时间</th></tr></thead><tbody>
      ${data.items.map((a) => `<tr><td class="mono">${esc(a.anchor_id)}</td>
        <td class="mono">${a.entry_from_seq}–${a.entry_to_seq}</td>
        <td class="mono">${esc(a.merkle_root.slice(0, 24))}…</td><td>${esc(a.chain)}</td>
        <td class="mono">${esc((a.tx_id || "").slice(0, 18))}…</td>
        <td class="mono">${esc(a.created_at.slice(0, 19))}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { toast(e.message, true); }
}

async function loadMetrics() {
  try {
    const m = await api("/api/admin/metrics", { admin: true });
    show($("metricsOut"), m);
  } catch (e) { toast(e.message, true); show($("metricsOut"), e.payload || e.message); }
}

$("btnMetrics").addEventListener("click", loadMetrics);
$("btnConsistency").addEventListener("click", async () => {
  try {
    const r = await api("/api/admin/consistency", { admin: true });
    show($("metricsOut"), r);
    toast(r.ledger_chain.valid && r.escrow.all_consistent ? "一致性核对通过" : "存在不一致，见下方输出", !(r.ledger_chain.valid && r.escrow.all_consistent));
  } catch (e) { toast(e.message, true); show($("metricsOut"), e.payload || e.message); }
});

(async function boot() {
  renderAdminState();
  loadHealth();
  try { await loadParties(); } catch (e) { toast("主体列表加载失败：" + e.message, true); }
  loadOrders();
  loadRules();
  loadModels();
})();
