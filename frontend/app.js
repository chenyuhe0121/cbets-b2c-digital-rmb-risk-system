/* CBETS-B2C 控制台：买家端 / 卖家端 / 平台端 分角色前端 */
'use strict';

/* ==================== 基础工具 ==================== */
const API = '';
const el = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const yuan = (cents) => '¥' + (Number(cents || 0) / 100).toFixed(2);

function adminKey() { return localStorage.getItem('cbets_admin_key') || ''; }

async function api(path, opt) {
  opt = opt || {};
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opt.headers || {});
  const k = adminKey();
  if (k) headers['X-Admin-Key'] = k;
  const res = await fetch(API + path, Object.assign({}, opt, { headers }));
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { raw: text }; }
  if (!res.ok) {
    let msg = 'HTTP ' + res.status;
    const d = data.detail;
    const e = data.error;
    if (typeof d === 'string') msg = d;
    else if (e && typeof e === 'object') msg = (e.code ? e.code + '：' : '') + (e.message || JSON.stringify(e));
    else if (d && typeof d === 'object') msg = (d.code ? d.code + '：' : '') + (d.message || JSON.stringify(d));
    throw new Error(msg);
  }
  return data;
}

function valHTML(v, k) {
  if (v === null || v === undefined) return '<span class="mut">—</span>';
  if (typeof v === 'number') {
    if (/cents/.test(k)) return '<b>' + yuan(v) + '</b><span class="mut"> · ' + v + ' 分</span>';
    if (/bps/.test(k)) return '<b>' + (v / 100).toFixed(2) + '%</b><span class="mut"> · ' + v + ' bps</span>';
    return '<b>' + v + '</b>';
  }
  if (typeof v === 'boolean') return '<b>' + (v ? '是' : '否') + '</b>';
  if (typeof v === 'string') {
    if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return '<b>' + esc(v.replace('T', ' ').slice(0, 19)) + '</b>';
    return '<b>' + esc(v) + '</b>';
  }
  const s = JSON.stringify(v);
  return '<span class="mut">' + esc(s.length > 260 ? s.slice(0, 260) + '…' : s) + '</span>';
}

function pretty(o) {
  if (o === null || o === undefined) return '<div class="mut">无数据</div>';
  if (typeof o !== 'object') return '<div><b>' + esc(String(o)) + '</b></div>';
  const rows = Object.entries(o).map(([k, v]) =>
    '<div class="kv"><span>' + esc(k) + '</span><div>' + valHTML(v, k) + '</div></div>');
  return rows.length ? rows.join('') : '<div class="mut">空</div>';
}

let toastTimer = null;
function toast(msg, ok) {
  const t = el('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'toast show ' + (ok === false ? 'bad' : 'good');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, 2800);
}

function on(id, ev, fn) {
  const n = el(id);
  if (!n) return;
  n.addEventListener(ev, fn);
}

function act(outId, fn) {
  return async (e) => {
    const b = e && e.currentTarget;
    if (b && b.tagName === 'BUTTON') b.disabled = true;
    try {
      const r = await fn();
      if (outId && el(outId) && r !== undefined) el(outId).innerHTML = pretty(r);
    } catch (err) {
      if (outId && el(outId)) el(outId).innerHTML = '<div class="err">' + esc(err.message) + '</div>';
      toast(err.message, false);
    } finally {
      if (b && b.tagName === 'BUTTON') b.disabled = false;
    }
  };
}

/* ==================== 角色与导航 ==================== */
const NOTES = {
  buyer: '买家端：下单、支付、签收、发起售后。发货、放款、裁决都不在买家端。',
  seller: '卖家端：发货、对恶意退款申诉、查担保账户并支取已结算货款。卖家不能直接退款。',
  platform: '平台端：风控、担保托管、纠纷仲裁、存证审计。平台不参与买卖。',
  flow: '业务流程总览：三条泳道分别是买家、卖家、平台与监管方，按编号 ①→⑫ 顺序读。'
};

const NAV = {
  buyer: [['b-shop', '下单与支付'], ['b-orders', '我的订单'], ['b-claim', '申请售后'], ['b-track', '售后进度']],
  seller: [['s-desk', '待发货工作台'], ['s-aftersales', '售后申诉'], ['s-escrow', '担保账户与结算']],
  platform: [['p-overview', '交易总览'], ['p-risk', '双引擎风控'], ['p-arbitrate', '纠纷仲裁'], ['p-audit', '存证审计'], ['p-metrics', '指标与一致性']],
  flow: [['flow', '业务链路图']]
};

let curRole = 'buyer';

function renderNav() {
  const nav = el('nav');
  if (!nav) return;
  nav.innerHTML = NAV[curRole].map(([id, label]) =>
    '<button data-view="' + id + '">' + label + '</button>').join('');
  nav.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => showView(b.dataset.view));
  });
}

function showView(id) {
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + id));
  document.querySelectorAll('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === id));
  onEnterView(id);
}

function switchRole(r) {
  curRole = r;
  document.body.dataset.role = r;
  document.querySelectorAll('#roleSwitch button').forEach((b) =>
    b.classList.toggle('active', b.dataset.role === r));
  if (el('roleNote')) el('roleNote').textContent = NOTES[r] || '';
  renderNav();
  showView(NAV[r][0][0]);
}

function onEnterView(id) {
  if (!partiesLoaded) loadParties().catch((e) => toast(e.message, false));
  if (id === 's-desk') loadSellerOrders().catch(() => { });
  if (id === 'p-overview') loadPlatformOverview().catch(() => { });
  if (id === 'p-risk') loadRules().catch(() => { });
  if (id === 'p-audit') loadAnchors().catch(() => { });
}

/* ==================== 业务链路图数据 ==================== */
const FLOW = [
  {
    n: 1, lane: 'buyer', t: '选品下单', s: '挑卖家、定 SKU 与金额',
    who: '买家', page: '买家端 · 下单与支付',
    act: 'POST /api/orders 创建订单，写入 orders 与合约初始状态 created，生成担保合约号',
    money: '不动钱。买家钱包余额不变，担保锁定额为 0，商户也拿不到钱',
    rule: '订单号可自定，用于演示幂等；金额必须大于 0；币种固定 CNY'
  },
  {
    n: 2, lane: 'buyer', t: '发起支付', s: '数字人民币付款进担保',
    who: '买家', page: '买家端 · 下单与支付',
    act: 'POST /api/orders/{id}/pay 先跑事中风控评分，通过后调 CBETS 收单，写 cbets_trades 与担保账本 LOCK 条目',
    money: '买家钱包扣款，货款进入商户担保子钱包 locked_cents，商户此刻取不出来',
    rule: '评分 ≥0.75 直接拦截；0.45–0.75 转人工复核并延长担保期；无风控记录的订单不能跳过'
  },
  {
    n: 3, lane: 'platform', t: '事前风控 + 担保入账', s: '规则库与模型双打分',
    who: '平台', page: '平台端 · 双引擎风控',
    act: '12 条规则逐个求值，命中项与逻辑回归概率加权成最终分，写 risk_records 并留解释字段',
    money: '通过后 LOCK 入账，锁定额记入商户担保子钱包，账本条目带 SHA-256 哈希',
    rule: '融合公式 final = 1-(1-p_model)(1-severity)；硬拦截规则直接短路，不看分数'
  },
  {
    n: 4, lane: 'seller', t: '接单备货', s: '看到已收款的待发货订单',
    who: '卖家', page: '卖家端 · 待发货工作台',
    act: 'GET /api/orders?seller_id= 拉本店订单，过滤 paid_escrowed 状态作为待发货队列',
    money: '只读。货款已在担保里，卖家看到的是「担保中」而不是「可提现」',
    rule: '卖家看不到买家的风控评分，避免按分数歧视客户'
  },
  {
    n: 5, lane: 'seller', t: '发货并上传运单', s: '填物流商与运单号',
    who: '卖家', page: '卖家端 · 待发货工作台',
    act: 'POST /api/orders/{id}/ship 生成 shipments 记录，写入揽收时间，合约状态转 shipped',
    money: '不动钱。担保继续锁定，直到签收或售后结论出来',
    rule: '揽收时间必须晚于下单时间，否则触发 S03 刷单规则'
  },
  {
    n: 6, lane: 'platform', t: '物流事件存证', s: '揽收、清关、签收逐步上链',
    who: '平台（物流适配器）', page: '平台端 · 存证审计',
    act: '物流回调写入 shipments 与 events，按时间戳落库，后续纠纷举证直接引用这条轨迹',
    money: '不动钱。但它是后续退款、拒付、丢件判定的唯一硬证据',
    rule: '运单号唯一；同一运单被两笔订单引用会被判定为虚假发货'
  },
  {
    n: 7, lane: 'buyer', t: '签收确认', s: '买家确认收到货',
    who: '买家', page: '买家端 · 我的订单',
    act: 'POST /api/orders/{id}/receive 合约转 received，担保进入结算倒计时',
    money: '钱仍在担保里，只是开始计时，签收后 72 小时自动释放',
    rule: '买家可以主动签收；超时未操作按物流签收时间自动推进'
  },
  {
    n: 8, lane: 'buyer', t: '申请售后', s: '五类场景各有举证要求',
    who: '买家', page: '买家端 · 申请售后',
    act: 'POST /api/orders/{id}/dispute 带 dtype 与 evidence，合约转 after_sales，生成纠纷号',
    money: '不动钱。担保锁定继续，等自动核验结论',
    rule: '7 天无理由必须在签收后 7 天内；举证不全不会直接驳回，而是转人工'
  },
  {
    n: 9, lane: 'seller', t: '售后申诉与举证', s: '对恶意退款反向主张',
    who: '卖家', page: '卖家端 · 售后申诉',
    act: 'POST /api/orders/{id}/dispute，dtype=malicious_refund、claimant=seller，附原始运单与签收证明',
    money: '主张成立则剩余锁定全额释放给商户；不成立则维持锁定转仲裁',
    rule: '卖家不能自己退款，只能举证；退款执行权在平台'
  },
  {
    n: 10, lane: 'platform', t: '证据核验与自动处置', s: '能验的当场结，验不了的转人工',
    who: '平台', page: '平台端 · 纠纷仲裁',
    act: 'classify() 按场景核验证据：退货签收、货损比例、虚假发货、丢件保价，给出退款与释放金额',
    money: '自动处置直接执行退款与释放，两条资金动作都写账本',
    rule: '退款必须等于当前剩余锁定额才允许全额退；部分退款与释放之和必须等于剩余额'
  },
  {
    n: 11, lane: 'platform', t: '放款或退款执行', s: '资金动作落地',
    who: '平台', page: '平台端 · 纠纷仲裁',
    act: 'release_to_seller 扣 0.6% 平台费后入商户结算余额；refund_to_buyer 退回买家钱包并调 CBETS 反向交易',
    money: '退款是反向新交易，不回滚原支付；释放的净额进 settled_cents，可提现',
    rule: '平台费 60 bps，按分向下取整；账户冻结时释放直接拒绝'
  },
  {
    n: 12, lane: 'platform', t: '哈希链与锚定', s: '整条链路可校验',
    who: '平台', page: '平台端 · 存证审计',
    act: 'GET /api/audit/verify 逐条重算哈希比对；POST /api/audit/anchor 把未锚定条目的 Merkle 根写进存证层',
    money: '不动钱。但任何人改动历史账本条目都会让校验失败',
    rule: '账本只追加不修改；锚定后连平台自己也无法悄悄改数'
  }
];

function renderLanes() {
  const lanes = { buyer: el('laneBuyer'), seller: el('laneSeller'), platform: el('lanePlatform') };
  Object.keys(lanes).forEach((key) => {
    const node = lanes[key];
    if (!node) return;
    const items = FLOW.filter((f) => f.lane === key).sort((a, b) => a.n - b.n);
    node.innerHTML = items.map((f, i) => {
      const card = '<button class="flowNode" data-fn="' + f.n + '">' +
        '<span class="fnNo">' + f.n + '</span>' +
        '<b>' + esc(f.t) + '</b>' +
        '<em>' + esc(f.s) + '</em></button>';
      return card + (i < items.length - 1 ? '<span class="laneArrow">→</span>' : '');
    }).join('');
    node.querySelectorAll('.flowNode').forEach((b) => {
      b.addEventListener('click', () => showFlowNode(b.dataset.fn));
    });
  });
}

function showFlowNode(num) {
  const f = FLOW.find((x) => String(x.n) === String(num));
  const box = el('flowDetail');
  if (!f || !box) return;
  document.querySelectorAll('.flowNode').forEach((b) =>
    b.classList.toggle('active', b.dataset.fn === String(num)));
  const laneName = { buyer: '买家泳道', seller: '卖家泳道', platform: '平台泳道' }[f.lane];
  box.innerHTML =
    '<div class="fdHead"><span class="fnNo">' + f.n + '</span><b>' + esc(f.t) + '</b>' +
    '<span class="chip">' + laneName + '</span></div>' +
    '<div class="fdRow"><span>谁操作</span><div>' + esc(f.who) + '</div></div>' +
    '<div class="fdRow"><span>原型位置</span><div><button class="linkBtn" data-jump="' + f.page + '">' + esc(f.page) + '</button></div></div>' +
    '<div class="fdRow"><span>系统动作</span><div>' + esc(f.act) + '</div></div>' +
    '<div class="fdRow"><span>资金变化</span><div>' + esc(f.money) + '</div></div>' +
    '<div class="fdRow"><span>规则约束</span><div>' + esc(f.rule) + '</div></div>';
  const jump = box.querySelector('[data-jump]');
  if (jump) {
    jump.addEventListener('click', () => {
      const page = jump.dataset.jump;
      const map = { '买家端': 'buyer', '卖家端': 'seller', '平台端': 'platform' };
      const role = Object.keys(map).find((k) => page.indexOf(k) === 0);
      if (role) {
        switchRole(map[role]);
        const target = { 
          '下单与支付': 'b-shop', '我的订单': 'b-orders', '申请售后': 'b-claim', '售后进度': 'b-track',
          '待发货工作台': 's-desk', '售后申诉': 's-aftersales', '担保账户与结算': 's-escrow',
          '交易总览': 'p-overview', '双引擎风控': 'p-risk', '纠纷仲裁': 'p-arbitrate',
          '存证审计': 'p-audit', '指标与一致性': 'p-metrics'
        };
        const key2 = Object.keys(target).find((k) => page.indexOf(k) >= 0);
        if (key2) showView(target[key2]);
      }
    });
  }
}

/* ==================== 主体数据 ==================== */
let partiesLoaded = false;
const PARTIES = { buyer: [], seller: [] };
let POLICIES = {};
let LAST_ORDER = '';

const STATUS = {
  created: '待支付', risk_blocked: '风控拦截', paid_escrowed: '已支付·担保中',
  shipped: '已发货', received: '已收货', after_sales: '售后中', arbitrating: '仲裁中',
  completed: '已完结', refunded: '已退款', partially_refunded: '部分退款', cancelled: '已取消'
};

function fillSel(node, items, labelFn) {
  if (!node) return;
  if (!items.length) { node.innerHTML = '<option value="">（无数据）</option>'; return; }
  node.innerHTML = items.map((it) =>
    '<option value="' + esc(it.party_id) + '">' + esc(labelFn(it)) + '</option>').join('');
}

async function loadParties() {
  if (partiesLoaded) return;
  const d = await api('/api/admin/parties');
  const items = d.items || [];
  PARTIES.buyer = items.filter((i) => i.role === 'buyer');
  PARTIES.seller = items.filter((i) => i.role === 'seller');
  fillSel(el('buyerIdentity'), PARTIES.buyer, (p) => p.name + '（' + p.party_id + '）');
  fillSel(el('shopSeller'), PARTIES.seller, (p) => p.name + '（' + p.party_id + '）');
  fillSel(el('sellerIdentity'), PARTIES.seller, (p) => p.name + '（' + p.party_id + '）');
  fillSel(el('escrowSellerSel'), PARTIES.seller, (p) => p.name + '（' + p.party_id + '）');
  fillSel(el('govParty'), PARTIES.buyer.concat(PARTIES.seller), (p) =>
    (p.role === 'buyer' ? '买家 ' : '卖家 ') + p.name + '（' + p.party_id + '）');
  partiesLoaded = true;
  rebuildSkus();
  renderBuyerProfile();
  try {
    POLICIES = await api('/api/policies');
    renderClaimFields();
  } catch (e) { /* 举证要求拿不到不影响其余功能 */ }
}

function buyerItem() {
  const id = el('buyerIdentity') ? el('buyerIdentity').value : '';
  return PARTIES.buyer.find((p) => p.party_id === id) || null;
}

function renderBuyerProfile() {
  const p = buyerItem();
  if (!p || !el('riskVerdict')) return;
  const pf = p.profile || {};
  const bits = [];
  if (pf.real_name_level !== undefined) bits.push('实名等级 ' + pf.real_name_level);
  if (pf.account_age_days !== undefined) bits.push('账户 ' + pf.account_age_days + ' 天');
  if (pf.refund_7d !== undefined) bits.push('近 7 天退款 ' + pf.refund_7d + ' 次');
  if (pf.chargeback_cnt !== undefined) bits.push('历史拒付 ' + pf.chargeback_cnt + ' 次');
  if (pf.fraud_tag) bits.push('欺诈标记');
  el('riskVerdict').innerHTML = '<div class="verdictIdle"><b>当前买家</b> ' + esc(p.party_id) +
    ' <span class="mut">钱包 ' + esc(p.wallet_id) + ' · ' + esc(bits.join(' / ') || '无异常标签') + '</span></div>';
}

/* 商品目录：原型固定三件，按卖家换一批 */
const SKU_CATALOG = {
  'SELL-DEMO-001': [
    { sku: 'SKU-A1', name: '真丝方巾 90cm', cents: 29900 },
    { sku: 'SKU-A2', name: '陶瓷茶具六件套', cents: 58800 },
    { sku: 'SKU-A3', name: '降噪蓝牙耳机 Pro', cents: 129900 }
  ],
  'SELL-DEMO-002': [
    { sku: 'SKU-B1', name: '折叠露营灯', cents: 19900 },
    { sku: 'SKU-B2', name: '户外保温壶 1.2L', cents: 45900 },
    { sku: 'SKU-B3', name: '便携投影仪', cents: 259900 }
  ]
};

function rebuildSkus() {
  const sel = el('shopSeller');
  const skuSel = el('shopSku');
  if (!sel || !skuSel) return;
  const list = SKU_CATALOG[sel.value] || SKU_CATALOG['SELL-DEMO-001'] || [];
  skuSel.innerHTML = list.map((s) =>
    '<option value="' + s.sku + '" data-cents="' + s.cents + '" data-name="' + esc(s.name) + '">' +
    esc(s.name) + ' · ' + yuan(s.cents) + '</option>').join('');
  syncAmountFromSku();
}

function syncAmountFromSku() {
  const skuSel = el('shopSku');
  if (!skuSel) return;
  const opt = skuSel.selectedOptions[0];
  if (opt && el('shopAmount')) el('shopAmount').value = (Number(opt.dataset.cents || 0) / 100).toFixed(2);
}

/* ==================== 买家端 ==================== */
async function createOrder() {
  const buyer = el('buyerIdentity').value;
  const seller = el('shopSeller').value;
  if (!buyer || !seller) throw new Error('买卖双方主体未加载');
  const amount = Math.round(parseFloat(el('shopAmount').value || '0') * 100);
  if (!(amount > 0)) throw new Error('订单金额必须大于 0');
  const opt = el('shopSku').selectedOptions[0];
  const body = {
    buyer_id: buyer, seller_id: seller, amount_cents: amount,
    sku_desc: (opt ? opt.dataset.name : '') + ' · ' + el('shopRegion').value + ' 直邮',
    currency: 'CNY'
  };
  const oid = el('shopOrderId').value.trim();
  if (oid) body.order_id = oid;
  const r = await api('/api/orders', { method: 'POST', body: JSON.stringify(body) });
  const order = r.order || r;
  LAST_ORDER = order.order_id;
  el('shopOrderId').value = LAST_ORDER;
  toast('订单已创建 ' + LAST_ORDER, true);
  return { 订单号: LAST_ORDER, 状态: STATUS[order.status] || order.status, 金额分: order.amount_cents };
}

async function riskPreview() {
  if (!LAST_ORDER) throw new Error('请先创建订单');
  const r = await api('/api/risk/preview?order_id=' + encodeURIComponent(LAST_ORDER));
  renderVerdict(r);
  return {
    decision: r.decision, final_score: r.final_score,
    buyer_prob: r.buyer_prob, seller_prob: r.seller_prob,
    rule_action: r.rule_action, 命中规则数: (r.hits || []).length,
    thresholds: r.thresholds
  };
}

function renderVerdict(r) {
  const box = el('riskVerdict');
  if (!box) return;
  const map = { pass: ['通过', 'ok'], review: ['转人工复核', 'warn'], block: ['拦截', 'bad'] };
  const pair = map[r.decision] || ['未知', ''];
  const hitsArr = r.hits || r.hit_rules || [];
  const hits = hitsArr.map((h) =>
    '<li><b>' + esc(h.rule_id) + '</b> ' + esc(h.name) +
    ' <span class="mut">' + esc(h.field) + '=' + esc(h.value) + '，阈值 ' + esc(h.threshold) +
    ' → ' + esc(h.action) + '</span></li>').join('');
  const th = r.thresholds;
  box.innerHTML =
    '<div class="verdict ' + pair[1] + '">' +
    '<div class="verdictTop"><span class="verdictTag">' + pair[0] + '</span>' +
    '<span class="verdictScore">' + (Number(r.final_score || 0) * 100).toFixed(1) + ' 分</span></div>' +
    '<div class="verdictBars">' +
    '<div><span>买家模型</span><b>' + (Number(r.buyer_prob || 0) * 100).toFixed(1) + '%</b></div>' +
    '<div><span>卖家模型</span><b>' + (Number(r.seller_prob || 0) * 100).toFixed(1) + '%</b></div>' +
    '<div><span>拦截/复核阈值</span><b>' + (th ? th.block + ' / ' + th.review : '—') + '</b></div>' +
    '</div>' +
    (hits ? '<ul class="hitList">' + hits + '</ul>' :
      (r.reasons && r.reasons.length ? '<div class="mut">' + esc(r.reasons.map(function (x) { return x.message; }).join('；')) + '</div>' :
        '<div class="mut">无规则命中</div>')) +
    '</div>';
}

async function payOrder() {
  if (!LAST_ORDER) throw new Error('请先创建订单');
  try {
    await api('/api/orders/' + encodeURIComponent(LAST_ORDER) + '/pay', { method: 'POST', body: '{}' });
  } catch (err) {
    const d = await api('/api/orders/' + encodeURIComponent(LAST_ORDER)).catch(() => null);
    if (d) {
      renderOrderDetail(d, 'shopOut');
      if (d.risk) renderVerdict(d.risk);
    }
    throw err;
  }
  const d = await api('/api/orders/' + encodeURIComponent(LAST_ORDER));
  renderOrderDetail(d, 'shopOut');
  if (d.risk) renderVerdict(d.risk);
  const c = d.contract || {};
  toast('支付成功，货款已进担保：' + yuan(c.locked_cents || 0), true);
  return;
}

function fmtTime(v) {
  return String(v || '').replace('T', ' ').replace('+00:00', '').slice(0, 19);
}

function renderOrderDetail(d, outId) {
  if (!outId || !el(outId)) return;
  const c = d.contract || {};
  const o = d.order || {};
  const head = '<div class="detailHead"><b>' + esc(o.order_id || '') + '</b>' +
    '<span class="tag st-' + esc(o.status || '') + '">' + esc(STATUS[o.status] || o.status || '') + '</span>' +
    '<span class="mut">' + esc(o.sku_desc || '') + ' · ' + yuan(o.amount_cents) + '</span></div>';
  const money = '<div class="moneyRow">' +
    '<div><span>担保锁定</span><b>' + yuan(c.locked_cents || 0) + '</b></div>' +
    '<div><span>已释放</span><b>' + yuan(c.released_cents || 0) + '</b></div>' +
    '<div><span>已退款</span><b>' + yuan(c.refunded_cents || 0) + '</b></div>' +
    '<div><span>剩余锁定</span><b>' + yuan((c.locked_cents || 0) - (c.released_cents || 0) - (c.refunded_cents || 0)) + '</b></div>' +
    '</div>';
  const ship = d.shipment ? '<div class="kv"><span>运单</span><div><b>' + esc(d.shipment.carrier || '') + ' ' +
    esc(d.shipment.tracking_no || '') + '</b><span class="mut"> 揽收 ' + esc(fmtTime(d.shipment.pickup_time)) +
    ' · ' + esc(d.shipment.status || '') + '</span></div></div>' : '';
  const rk = d.risk ? '<div class="kv"><span>风控结论</span><div><b>' +
    ({ pass: '通过', review: '转人工复核', block: '拦截' }[d.risk.decision] || d.risk.decision) +
    '</b><span class="mut"> 最终分 ' + (Number(d.risk.final_score || 0) * 100).toFixed(1) +
    ' · 买家模型 ' + (Number(d.risk.buyer_prob || 0) * 100).toFixed(1) + '%' +
    ' · 卖家模型 ' + (Number(d.risk.seller_prob || 0) * 100).toFixed(1) + '%</span></div></div>' : '';
  const ct = d.contract ? '<div class="kv"><span>合约状态</span><div><b>' + esc(c.status || '') +
    '</b><span class="mut"> 担保到期 ' + esc(fmtTime(o.hold_until)) + '</span></div></div>' : '';
  const led = (d.ledger || []).map((e) =>
    '<div class="ledRow"><span class="chip">' + esc(e.entry_type) + '</span>' +
    '<b>' + yuan(e.amount_cents) + '</b><span class="mut">' + esc(e.memo || '') + '</span>' +
    '<span class="hash">' + esc((e.entry_hash || '').slice(0, 12)) + '…</span></div>').join('');
  const dp = (d.disputes || []).map((x) =>
    '<div class="ledRow"><span class="chip">纠纷</span><b>' + esc(x.dispute_id) + '</b>' +
    '<span class="mut">' + esc(x.dtype) + ' · ' + esc(x.status) + '</span></div>').join('');
  el(outId).innerHTML = head + money + ship + rk + ct +
    (led ? '<div class="subTitle">账本条目</div>' + led : '') +
    (dp ? '<div class="subTitle">纠纷记录</div>' + dp : '');
}

function orderRow(o, side) {
  const st = o.status || '';
  let btns = '<button class="mini ghost" data-act="detail" data-id="' + esc(o.order_id) + '">详情</button>';
  if (side === 'buyer' && st === 'shipped') {
    btns += '<button class="mini" data-act="receive" data-id="' + esc(o.order_id) + '">确认收货</button>';
  }
  if (side === 'seller' && st === 'paid_escrowed') {
    btns += '<button class="mini" data-act="ship" data-id="' + esc(o.order_id) + '">发货</button>';
  }
  const other = side === 'buyer' ? '卖家 ' + esc(o.seller_id) : '买家 ' + esc(o.buyer_id);
  return '<div class="orderRow"><div class="orderMain">' +
    '<b>' + esc(o.order_id) + '</b>' +
    '<span class="tag st-' + esc(st) + '">' + esc(STATUS[st] || st) + '</span>' +
    '<span class="mut">' + esc(o.sku_desc || '') + ' · ' + yuan(o.amount_cents) + '</span>' +
    '</div><div class="orderSide"><span class="mut">' + other + '</span>' + btns + '</div></div>';
}

async function loadBuyerOrders() {
  const bid = el('buyerIdentity').value;
  if (!bid) { el('buyerOrders').innerHTML = '<div class="empty">没有可用买家</div>'; return; }
  const d = await api('/api/orders?buyer_id=' + encodeURIComponent(bid) + '&limit=50');
  const items = d.items || [];
  el('buyerOrders').innerHTML = items.length
    ? items.map((o) => orderRow(o, 'buyer')).join('')
    : '<div class="empty">该买家还没有订单，先去「下单与支付」建一笔</div>';
}

async function loadSellerOrders() {
  const sid = el('sellerIdentity') ? el('sellerIdentity').value : '';
  if (!el('sellerOrders')) return;
  if (!sid) { el('sellerOrders').innerHTML = '<div class="empty">没有可用卖家</div>'; return; }
  const d = await api('/api/orders?seller_id=' + encodeURIComponent(sid) + '&limit=50');
  const items = d.items || [];
  const todo = items.filter((o) => o.status === 'paid_escrowed');
  const rest = items.filter((o) => o.status !== 'paid_escrowed');
  let h = '<div class="subTitle">待发货 ' + todo.length + ' 笔</div>';
  h += todo.length ? todo.map((o) => orderRow(o, 'seller')).join('')
    : '<div class="empty">没有待发货订单，买家支付后会出现在这里</div>';
  h += '<div class="subTitle">本店其他订单</div>';
  h += rest.length ? rest.map((o) => orderRow(o, 'seller')).join('')
    : '<div class="empty">暂无</div>';
  el('sellerOrders').innerHTML = h;
}

function bindOrderList(node, outId, fillId, reload) {
  if (!node) return;
  node.addEventListener('click', async (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    const id = b.dataset.id;
    try {
      if (b.dataset.act === 'detail') {
        const d = await api('/api/orders/' + encodeURIComponent(id));
        if (fillId && el(fillId)) el(fillId).value = id;
        LAST_ORDER = id;
        renderOrderDetail(d, outId);
      } else if (b.dataset.act === 'receive') {
        await api('/api/orders/' + encodeURIComponent(id) + '/receive', { method: 'POST' });
        toast('已确认收货，担保开始倒计时', true);
        const d = await api('/api/orders/' + encodeURIComponent(id));
        renderOrderDetail(d, outId);
        if (reload) await reload();
      } else if (b.dataset.act === 'ship') {
        if (el('shipOrderId')) el('shipOrderId').value = id;
        toast('已填入订单号，补运单号后点确认发货', true);
      }
    } catch (err) {
      if (outId && el(outId)) el(outId).innerHTML = '<div class="err">' + esc(err.message) + '</div>';
      toast(err.message, false);
    }
  });
}

/* 售后举证字段定义 */
const EVID = {
  no_reason_return: [
    { k: 'return_signed', label: '退货已签收', type: 'check' },
    { k: 'return_tracking_no', label: '退货物流单号', type: 'text', ph: 'YT202611180001' }
  ],
  partial_damage: [
    { k: 'photos', label: '破损照片凭证（逗号分隔）', type: 'text', ph: 'IMG-001, IMG-002', list: true },
    { k: 'damage_ratio', label: '货损比例 0-1', type: 'number', def: 0.3 }
  ],
  logistics_loss: [
    { k: 'loss_confirmed', label: '物流方已确认丢件/破损', type: 'check' },
    { k: 'insured', label: '已保价', type: 'check' }
  ],
  fake_delivery: [
    { k: 'tracking_no', label: '平台运单号', type: 'text', ph: 'YW202611180001' }
  ]
};

function renderClaimFields() {
  const box = el('claimPolicy');
  const sel = el('claimType');
  if (!box || !sel) return;
  const t = sel.value;
  const p = (POLICIES || {})[t] || {};
  const fields = EVID[t] || [];
  let h = '<div class="policyHead"><b>' + esc(p.label || t) + '</b>' +
    '<span class="mut">自动处置：' + esc(p.auto || '—') + '</span></div>';
  h += '<div class="policyReq">举证要求：' +
    ((p.evidence || []).map((x) => '<span class="chip">' + esc(x) + '</span>').join('') || '<span class="mut">无</span>') +
    '</div>';
  h += '<div class="grid c3" style="margin-top:10px">' + fields.map((f) => {
    if (f.type === 'check') {
      return '<div><label>' + esc(f.label) + '</label><label class="checkLine">' +
        '<input type="checkbox" data-k="' + f.k + '"> 已具备</label></div>';
    }
    if (f.type === 'number') {
      return '<div><label>' + esc(f.label) + '</label><input data-k="' + f.k +
        '" type="number" step="0.01" min="0" max="1" value="' + f.def + '"></div>';
    }
    return '<div><label>' + esc(f.label) + '</label><input data-k="' + f.k +
      '" placeholder="' + esc(f.ph || '') + '"></div>';
  }).join('') + '</div>';
  box.innerHTML = h;
}

function collectEvidence() {
  const t = el('claimType').value;
  const defs = EVID[t] || [];
  const ev = {};
  const box = el('claimPolicy');
  if (box) {
    box.querySelectorAll('[data-k]').forEach((inp) => {
      const k = inp.dataset.k;
      const def = defs.find((d) => d.k === k) || {};
      if (inp.type === 'checkbox') { if (inp.checked) ev[k] = true; return; }
      const v = inp.value.trim();
      if (v === '') return;
      if (inp.type === 'number') { ev[k] = parseFloat(v); return; }
      ev[k] = def.list ? v.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean) : v;
    });
  }
  const note = el('claimNote') ? el('claimNote').value.trim() : '';
  if (note) ev.buyer_note = note;
  return ev;
}

async function openClaim() {
  const oid = el('claimOrderId').value.trim();
  if (!oid) throw new Error('请填订单号');
  const dtype = el('claimType').value;
  const evidence = collectEvidence();
  const r = await api('/api/orders/' + encodeURIComponent(oid) + '/dispute', {
    method: 'POST',
    body: JSON.stringify({ dtype: dtype, claimant: 'buyer', evidence: evidence })
  });
  el('trackDisputeId').value = r.dispute_id;
  renderTimeline(r);
  if (r.auto) {
    toast('已自动处置：' + (r.note || ''), true);
  } else {
    toast('转人工仲裁：' + (r.reason || ''), false);
  }
  return {
    纠纷号: r.dispute_id,
    结论: r.auto ? '证据可核验，已自动处置' : '证据不足，转人工仲裁',
    说明: r.note || r.reason,
    退款分: r.refund_cents,
    释放分: r.release_cents,
    场景: (r.policies && r.policies.label) || dtype
  };
}

function renderTimeline(d) {
  const box = el('trackTimeline');
  if (!box) return;
  const steps = [
    { t: '提交申请', v: d.created_at, ok: true },
    { t: '当前状态', v: d.status === 'resolved' ? '已裁决' : ((d.status === 'arbitration' || d.status === 'arbitrating') ? '人工仲裁中' : '处理中'), ok: d.status === 'resolved' },
    { t: '裁决说明', v: (d.decision && (d.decision.note || d.decision.reason)) || '—', ok: d.status === 'resolved' }
  ];
  box.innerHTML = steps.map((s) =>
    '<div class="tlStep ' + (s.ok ? 'ok' : '') + '"><span class="tlDot"></span>' +
    '<div><b>' + esc(s.t) + '</b><em>' + esc(String(s.v || '—').replace('T', ' ').slice(0, 19)) + '</em></div></div>'
  ).join('');
}

/* ==================== 卖家端 ==================== */
async function shipOrder() {
  const oid = el('shipOrderId').value.trim();
  const tracking = el('shipTracking').value.trim();
  if (!oid) throw new Error('请填订单号');
  if (!tracking) throw new Error('发货必须填运单号');
  await api('/api/orders/' + encodeURIComponent(oid) + '/ship', {
    method: 'POST',
    body: JSON.stringify({ carrier: el('shipCarrier').value, tracking_no: tracking })
  });
  toast('已发货，运单 ' + tracking, true);
  await loadSellerOrders();
  const d = await api('/api/orders/' + encodeURIComponent(oid));
  renderOrderDetail(d, 'sellerOut');
  return;
}

async function sellerClaim() {
  const oid = el('sellerClaimOrderId').value.trim();
  if (!oid) throw new Error('请填订单号');
  const tracking = el('sellerClaimTracking').value.trim();
  if (!tracking) throw new Error('请填原始运单号');
  const evidence = { tracking_no: tracking, signed_receipt: el('sellerClaimSigned').checked };
  const r = await api('/api/orders/' + encodeURIComponent(oid) + '/dispute', {
    method: 'POST',
    body: JSON.stringify({ dtype: 'malicious_refund', claimant: 'seller', evidence: evidence })
  });
  if (el('sellerDisputeId')) el('sellerDisputeId').value = r.dispute_id;
  toast(r.auto ? '申诉成立，货款全额释放' : '转平台仲裁：' + (r.reason || ''), r.auto === true);
  return {
    纠纷号: r.dispute_id,
    结论: r.auto ? '申诉成立，自动处置' : '主张不成立，转人工仲裁',
    说明: r.note || r.reason,
    退款分: r.refund_cents,
    释放分: r.release_cents,
    平台手续费分: r.fee_cents,
    场景: '买家恶意退款 / 虚假拒付'
  };
}

async function loadEscrow() {
  const sid = el('escrowSellerSel').value;
  if (!sid) throw new Error('请选择商户');
  const d = await api('/api/escrow/' + encodeURIComponent(sid));
  const a = d.account || {};
  const cards = el('escrowCards');
  if (cards) {
    cards.innerHTML = '<div class="escCard lock"><span>担保中（不可提）</span><b>' + yuan(a.locked_cents) + '</b></div>' +
      '<div class="escCard ok"><span>已结算（可提）</span><b>' + yuan(a.settled_cents) + '</b></div>' +
      '<div class="escCard bad"><span>已退款给买家</span><b>' + yuan(a.refunded_cents) + '</b></div>' +
      '<div class="escCard"><span>账户状态</span><b>' + (a.frozen ? '冻结' : '正常') + '</b></div>';
  }
  const entries = d.entries || [];
  if (el('escrowLedger')) {
    el('escrowLedger').innerHTML = entries.length
      ? entries.map((e) => '<div class="ledRow"><span class="chip">' + esc(e.entry_type) + '</span>' +
        '<b>' + yuan(e.amount_cents) + '</b><span class="mut">' + esc(e.memo || '') + '</span>' +
        '<span class="hash">' + esc((e.entry_hash || '').slice(0, 12)) + '…</span></div>').join('')
      : '<div class="empty">还没有账本条目</div>';
  }
  const rec = d.reconcile || {};
  return {
    商户: sid, 担保中: a.locked_cents, 已结算: a.settled_cents, 已退款: a.refunded_cents,
    账户冻结: !!a.frozen, 对账一致: rec.consistent,
    账本条目数: entries.length
  };
}

async function withdraw() {
  const sid = el('escrowSellerSel').value;
  if (!sid) throw new Error('请选择商户');
  const r = await api('/api/escrow/' + encodeURIComponent(sid) + '/withdraw', {
    method: 'POST', body: JSON.stringify({ amount_cents: 0 })
  });
  toast('已提现 ' + yuan(r.withdrawn_cents) + '，结算余额剩 ' + yuan(r.settled_cents), true);
  await loadEscrow();
  return {
    提现编号: r.ref_no,
    本次提现分: r.withdrawn_cents,
    结算余额分: r.settled_cents,
    担保中分: r.locked_cents,
    到账钱包: (r.payout && r.payout.ok) ? '成功' : '失败'
  };
}

/* ==================== 平台端 ==================== */
function statCards(m) {
  const o = m.orders || {}, money = m.money || {}, risk = m.risk || {},
    as = m.after_sales || {}, ledger = m.ledger || {};
  const cards = [
    ['订单总数', o.total || 0, '已支付 ' + (o.paid || 0)],
    ['拦截订单', o.risk_blocked || 0, '拦截率 ' + ((risk.block_rate || 0) * 100).toFixed(1) + '%'],
    ['担保冻结中', yuan(money.escrow_locked_cents), 'GMV ' + yuan(money.gmv_cents)],
    ['已释放给商户', yuan(money.released_gross_cents), '已退款 ' + yuan(money.refunded_cents)],
    ['纠纷 / 待仲裁', (as.disputes || 0) + ' / ' + (as.arbitrating || 0), '平均风险分 ' + ((Number(risk.avg_score || 0)) * 100).toFixed(1)],
    ['账本哈希链', ledger.valid ? '校验通过' : '校验异常', '条目 ' + (ledger.entries == null ? '—' : ledger.entries)],
    ['平台服务费', ((money.platform_fee_bps || 0) / 100).toFixed(2) + '%', '按分向下取整'],
    ['风控记录', risk.records || 0, '模型 ' + ((m.models && m.models.buyer) || '—')]
  ];
  return cards.map(([k, v, s]) =>
    '<div class="stat"><span>' + esc(k) + '</span><b>' + esc(String(v)) + '</b><em>' + esc(String(s)) + '</em></div>').join('');
}

async function loadPlatformOverview() {
  const m = await api('/api/admin/metrics');
  if (el('platformStats')) el('platformStats').innerHTML = statCards(m);
  const d = await api('/api/orders?limit=60');
  const items = d.items || [];
  if (el('platformOrders')) {
    el('platformOrders').innerHTML = items.length
      ? items.map((o) => orderRow(o, 'platform')).join('')
      : '<div class="empty">还没有订单</div>';
  }
  return { 订单总数: (m.orders || {}).total, 拦截率: (m.risk || {}).block_rate, 账本校验: (m.ledger || {}).valid };
}

async function loadRules() {
  const d = await api('/api/risk/rules');
  const items = d.items || [];
  const box = el('rulesTable');
  if (box) {
    box.innerHTML = '<table class="tbl"><thead><tr><th>规则</th><th>对象</th><th>触发条件</th>' +
      '<th>动作</th><th>启用</th></tr></thead><tbody>' +
      items.map((r) => {
        const th = Array.isArray(r.threshold) ? r.threshold.join(' ~ ') : r.threshold;
        return '<tr><td><b>' + esc(r.rule_id) + '</b><br><span class="mut">' + esc(r.name) + '</span></td>' +
        '<td>' + (r.scope === 'buyer' ? '买家' : '卖家') + '</td>' +
        '<td><span class="mut">' + esc(r.field || '') + ' ' + esc(r.op || '') + ' ' + esc(th) + '</span></td>' +
        '<td><span class="chip">' + esc(r.action) + '</span></td>' +
        '<td><button class="mini ' + (r.enabled ? '' : 'ghost') + '" data-rule="' + esc(r.rule_id) +
        '" data-en="' + (r.enabled ? '1' : '0') + '">' + (r.enabled ? '已启用' : '已停用') + '</button></td></tr>';
      }).join('') +
      '</tbody></table>';
    box.querySelectorAll('[data-rule]').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          await api('/api/risk/rules/' + encodeURIComponent(b.dataset.rule), {
            method: 'PATCH', body: JSON.stringify({ enabled: b.dataset.en === '1' ? false : true })
          });
          toast('规则 ' + b.dataset.rule + ' 已切换', true);
          await loadRules();
        } catch (err) { toast(err.message, false); }
      });
    });
  }
  try {
    const mi = await api('/api/risk/models');
    if (el('modelInfo')) el('modelInfo').innerHTML = pretty(mi);
  } catch (e) { /* 模型信息拿不到不影响规则表 */ }
  return { 规则数: items.length, 启用: items.filter((r) => r.enabled).length };
}

async function loadDisputeForArb() {
  const id = el('arbDisputeId').value.trim();
  if (!id) throw new Error('请填纠纷号');
  const d = await api('/api/disputes/' + encodeURIComponent(id));
  const od = await api('/api/orders/' + encodeURIComponent(d.order_id));
  const c = od.contract || {};
  const remaining = (c.locked_cents || 0) - (c.released_cents || 0) - (c.refunded_cents || 0);
  if (el('arbRefund')) el('arbRefund').value = 0;
  if (el('arbRelease')) el('arbRelease').value = remaining;
  if (el('arbOut')) {
    el('arbOut').innerHTML = '<div class="hintLine">剩余锁定 <b>' + yuan(remaining) + '</b>（' + remaining +
      ' 分），退款额 + 释放额必须正好等于这个数，否则后端直接拒绝。</div>' +
      pretty({ 纠纷号: d.dispute_id, 订单号: d.order_id,
        场景: ((POLICIES || {})[d.dtype] || {}).label || d.dtype,
        申请人: d.claimant === 'seller' ? '卖家' : '买家',
        状态: d.status === 'arbitration' ? '人工仲裁中' : d.status,
        证据: (d.evidence && Object.keys(d.evidence).length) ? JSON.stringify(d.evidence) : '（未提交证据）',
        当前裁决: (d.decision && (d.decision.reason || d.decision.note)) || '—',
        剩余锁定分: remaining, 合约状态: c.status });
  }
  return;
}

async function arbitrate() {
  const id = el('arbDisputeId').value.trim();
  if (!id) throw new Error('请填纠纷号');
  const refund = parseInt(el('arbRefund').value || '0', 10);
  const release = parseInt(el('arbRelease').value || '0', 10);
  const reviewer = el('arbReviewer').value.trim() || '合规-演示';
  const r = await api('/api/disputes/' + encodeURIComponent(id) + '/arbitrate', {
    method: 'POST',
    body: JSON.stringify({ refund_cents: refund, release_cents: release, reviewer: reviewer, note: el('arbNote').value })
  });
  toast('裁决完成，退款 ' + yuan(refund) + '，释放 ' + yuan(release), true);
  return r;
}

async function verifyChain() {
  const r = await api('/api/audit/verify');
  toast(r.valid ? '哈希链校验通过' : '校验失败，断点 seq=' + r.broken_at_seq, r.valid);
  return {
    校验结果: r.valid ? '哈希链完整，未被篡改' : '校验失败，第 ' + r.broken_at_seq + ' 条起断链',
    失败原因: r.reason || '无',
    账本条目数: r.entries,
    链头哈希: r.head_hash
  };
}

async function anchorChain() {
  const r = await api('/api/audit/anchor', { method: 'POST', body: '{}' });
  toast(r.result === 'nothing_to_anchor' ? '没有新的账本条目可锚定' : '已锚定 ' + (r.entries || 0) + ' 条', true);
  await loadAnchors();
  if (r.result === 'nothing_to_anchor') return { 结果: '没有新的账本条目', 说明: '上次锚定之后没有新增账本条目' };
  return {
    批次号: r.batch_id,
    账本区间: 'seq ' + r.from_seq + ' – ' + r.to_seq,
    条目数: r.entries,
    'Merkle 根': r.merkle_root,
    存证交易: (r.anchor && r.anchor.tx_id) || '—',
    存证网络: (r.anchor && r.anchor.chain) || '—',
    区块高度: (r.anchor && r.anchor.block_height) || '—'
  };
}

async function loadAnchors() {
  const d = await api('/api/audit/anchors');
  const items = d.items || [];
  const box = el('anchorsTable');
  if (!box) return;
  box.innerHTML = items.length
    ? '<table class="tbl"><thead><tr><th>批次</th><th>账本区间</th><th>Merkle 根</th><th>存证网络</th><th>时间</th></tr></thead><tbody>' +
      items.map((a) => '<tr><td><b>' + esc(a.anchor_id) + '</b></td><td>seq ' + esc(a.entry_from_seq) + ' – ' +
        esc(a.entry_to_seq) + '</td><td><span class="hash">' + esc((a.merkle_root || '').slice(0, 18)) + '…</span></td><td>' +
        esc(a.chain || '—') + '</td><td>' + esc(fmtTime(a.created_at)) + '</td></tr>').join('') + '</tbody></table>'
    : '<div class="empty">还没有锚定批次，点上面「锚定未上链部分」</div>';
}

async function loadMetrics() {
  const m = await api('/api/admin/metrics');
  if (el('platformStats')) el('platformStats').innerHTML = statCards(m);
  const o = m.orders || {}, money = m.money || {}, risk = m.risk || {},
    as_ = m.after_sales || {}, lg = m.ledger || {};
  return {
    订单总数: o.total,
    已支付订单: o.paid,
    状态分布: Object.entries(o.by_status || {}).map(function (e) {
      return (STATUS[e[0]] || e[0]) + ' ' + e[1];
    }).join(' / ') || '—',
    GMV: yuan(money.gmv_cents),
    担保冻结中: yuan(money.escrow_locked_cents),
    已释放总额: yuan(money.released_gross_cents),
    已退款总额: yuan(money.refunded_cents),
    风控记录数: risk.records,
    平均风险分: (Number(risk.avg_score || 0) * 100).toFixed(1),
    拦截率: (Number(risk.block_rate || 0) * 100).toFixed(1) + '%',
    纠纷数: as_.disputes,
    待仲裁: as_.arbitrating,
    账本校验: lg.valid ? '通过' : '失败',
    买家模型: (m.models || {}).buyer,
    卖家模型: (m.models || {}).seller
  };
}

async function consistency() {
  const r = await api('/api/admin/consistency');
  const ec = r.escrow || {}, wc = r.wallet_credits || {};
  return {
    账本哈希链: (r.ledger_chain || {}).valid ? '完整' : '断链',
    担保余额对账: ec.all_consistent ? '全部一致' : '存在不一致',
    缺失的履约入账: (wc.missing_release || []).length,
    缺失的退款入账: (wc.missing_refund || []).length,
    明细: (ec.checks || []).map(function (c) {
      return c.seller_id + '：账户 ' + yuan(c.account_locked_cents) + ' / 合约 ' + yuan(c.contract_locked_cents) +
        (c.consistent ? ' 一致' : ' 不一致');
    }).join('；') || '—'
  };
}

/* ==================== 启动 ==================== */
async function boot() {
  renderLanes();

  const rs = el('roleSwitch');
  if (rs) {
    rs.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', () => switchRole(b.dataset.role));
    });
  }

  on('adminState', 'click', () => {
    const v = window.prompt('输入管理员密钥（线上部署配置了 CBETS_ADMIN_KEY 时才需要）', adminKey());
    if (v === null) return;
    if (v.trim() === '') localStorage.removeItem('cbets_admin_key');
    else localStorage.setItem('cbets_admin_key', v.trim());
    updateAdminState();
    toast('管理员密钥已' + (v.trim() ? '设置' : '清空'), true);
  });

  /* 买家 */
  on('buyerIdentity', 'change', () => { renderBuyerProfile(); loadBuyerOrders().catch(() => { }); });
  on('shopSeller', 'change', rebuildSkus);
  on('shopSku', 'change', syncAmountFromSku);
  on('btnCreateOrder', 'click', act('shopOut', createOrder));
  on('btnRiskPreview', 'click', act('shopOut', riskPreview));
  on('btnPayOrder', 'click', act('shopOut', payOrder));
  on('btnTopupBuyer', 'click', act('shopOut', async () => {
    const p = buyerItem();
    if (!p) throw new Error('请选择买家');
    const r = await api('/api/admin/wallets/topup', {
      method: 'POST', body: JSON.stringify({ wallet_id: p.wallet_id, amount_cents: 100000 })
    });
    toast('已充值，余额 ' + yuan(r.balance_cents), true);
    return r;
  }));
  on('btnBuyerRefresh', 'click', act('buyerOrderOut', loadBuyerOrders));
  on('btnBuyerDetail', 'click', act('buyerOrderOut', async () => {
    const id = el('buyerOrderId').value.trim();
    if (!id) throw new Error('请填订单号');
    const d = await api('/api/orders/' + encodeURIComponent(id));
    renderOrderDetail(d, 'buyerOrderOut');
    return null;
  }));
  on('btnBuyerReceive', 'click', act('buyerOrderOut', async () => {
    const id = el('buyerOrderId').value.trim();
    if (!id) throw new Error('请填订单号');
    const r = await api('/api/orders/' + encodeURIComponent(id) + '/receive', { method: 'POST' });
    toast('已确认收货', true);
    await loadBuyerOrders();
    return r;
  }));
  on('claimType', 'change', renderClaimFields);
  on('btnOpenClaim', 'click', act('claimOut', openClaim));
  on('btnTrack', 'click', act('trackOut', async () => {
    const id = el('trackDisputeId').value.trim();
    if (!id) throw new Error('请填纠纷号');
    const d = await api('/api/disputes/' + encodeURIComponent(id));
    renderTimeline(d);
    return d;
  }));

  /* 卖家 */
  on('sellerIdentity', 'change', () => {
    if (el('escrowSellerSel')) el('escrowSellerSel').value = el('sellerIdentity').value;
    loadSellerOrders().catch(() => { });
  });
  on('escrowSellerSel', 'change', () => {
    if (el('sellerIdentity')) el('sellerIdentity').value = el('escrowSellerSel').value;
  });
  on('btnSellerRefresh', 'click', act('sellerOut', loadSellerOrders));
  on('btnShipOrder', 'click', act('sellerOut', shipOrder));
  on('btnSellerClaim', 'click', act('sellerClaimOut', sellerClaim));
  on('btnSellerDisputeQuery', 'click', act('sellerDisputeOut', async () => {
    const id = el('sellerDisputeId').value.trim();
    if (!id) throw new Error('请填纠纷号');
    return api('/api/disputes/' + encodeURIComponent(id));
  }));
  on('btnLoadEscrow', 'click', act(null, loadEscrow));
  on('btnWithdraw', 'click', act(null, withdraw));

  /* 平台 */
  on('btnPlatformRefresh', 'click', act(null, loadPlatformOverview));
  on('btnGovSet', 'click', act('govOut', async () => {
    const pid = el('govParty').value;
    if (!pid) throw new Error('请选择主体');
    const r = await api('/api/admin/parties/' + encodeURIComponent(pid) + '/flags', {
      method: 'POST',
      body: JSON.stringify({
        field: el('govField').value,
        value: parseInt(el('govValue').value || '0', 10)
      })
    });
    partiesLoaded = false;
    await loadParties();
    toast('已更新 ' + pid + ' 的 ' + el('govField').value, true);
    return r;
  }));
  on('btnReloadRules', 'click', act(null, loadRules));
  on('btnArbQuery', 'click', act(null, loadDisputeForArb));
  on('btnArbitrate', 'click', act('arbOut', arbitrate));
  on('btnVerify', 'click', act('auditOut', verifyChain));
  on('btnAnchor', 'click', act('auditOut', anchorChain));
  on('btnMetrics', 'click', act('metricsOut', loadMetrics));
  on('btnConsistency', 'click', act('metricsOut', consistency));

  bindOrderList(el('buyerOrders'), 'buyerOrderOut', 'buyerOrderId', loadBuyerOrders);
  bindOrderList(el('sellerOrders'), 'sellerOut', 'shipOrderId', loadSellerOrders);

  updateAdminState();

  try {
    const h = await api('/healthz');
    el('healthPill').textContent = '服务正常 · ' + (h.adapter_mode || 'mock');
    el('healthPill').className = 'pill ok';
  } catch (e) {
    el('healthPill').textContent = '后端未连接';
    el('healthPill').className = 'pill bad';
  }

  switchRole('buyer');

  try {
    await loadParties();
    await loadBuyerOrders();
  } catch (e) {
    toast('主体数据加载失败：' + e.message, false);
  }
}

function updateAdminState() {
  const n = el('adminState');
  if (!n) return;
  n.textContent = adminKey() ? '管理员密钥：已设置' : '管理员密钥：未设置';
  n.className = 'pill' + (adminKey() ? ' ok' : '');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
