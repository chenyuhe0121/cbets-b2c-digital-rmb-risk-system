"""HTTP 层端到端自检：把服务当黑盒，只走真实接口。

用法：
    python main.py            # 另开一个终端
    python scripts/e2e_http.py http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
ADMIN_KEY = ""
steps: list[str] = []


def call(path: str, method: str = "GET", body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          **({"X-Admin-Key": ADMIN_KEY} if ADMIN_KEY else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def call_raw(path: str) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def ok(label: str, detail: str = "") -> None:
    steps.append(label)
    print(f"[PASS] {label}{(' — ' + detail) if detail else ''}")


def must(status: int, expected: int, payload: dict, label: str) -> dict:
    assert status == expected, f"{label}: HTTP {status} {payload}"
    return payload


def main() -> None:
    status, health = call("/healthz")
    must(status, 200, health, "健康检查")
    ok("健康检查", health["status"])

    status, parties = call("/api/admin/parties")
    must(status, 200, parties, "主体列表")
    buyer = next(p for p in parties["items"] if p["party_id"] == "BUY-DEMO-001")
    seller = next(p for p in parties["items"] if p["party_id"] == "SELL-DEMO-001")
    ok("主体列表", f"{len(parties['items'])} 个主体")

    # 1) 正常链路
    status, order = call("/api/orders", "POST", {
        "buyer_id": buyer["party_id"], "seller_id": seller["party_id"],
        "amount_cents": 29900, "sku_desc": "HTTP 联调订单",
    })
    oid = must(status, 201, order, "创建订单")["order"]["order_id"]
    ok("创建订单", oid)

    status, preview = call(f"/api/risk/preview?order_id={oid}")
    must(status, 200, preview, "事前风控")
    ok("事前风控", f"score={preview['final_score']} decision={preview['decision']}")

    status, paid = call(f"/api/orders/{oid}/pay", "POST", {})
    must(status, 200, paid, "支付")
    assert paid["result"] == "paid", paid
    ok("支付", f"trade={paid['payment']['trade_no']}")

    status, detail = call(f"/api/orders/{oid}")
    must(status, 200, detail, "订单详情")
    assert detail["contract"]["status"] == "locked", detail["contract"]
    assert detail["ledger"][0]["entry_type"] == "LOCK"
    ok("担保入账", f"locked={detail['contract']['locked_cents']}")

    status, shipped = call(f"/api/orders/{oid}/ship", "POST",
                           {"carrier": "燕文", "tracking_no": "HT" + oid[-8:]})
    must(status, 200, shipped, "发货")
    status, received = call(f"/api/orders/{oid}/receive", "POST", {})
    must(status, 200, received, "收货")
    status, released = call(f"/api/orders/{oid}/release", "POST", {})
    must(status, 200, released, "释放")
    ok("履约释放", f"gross={released['gross_cents']} fee={released['fee_cents']} net={released['net_cents']}")

    # 2) 风控拦截
    status, blocked = call("/api/orders", "POST", {
        "buyer_id": "BUY-DEMO-002", "seller_id": seller["party_id"],
        "amount_cents": 18800, "sku_desc": "高风险买家订单",
    })
    bid = must(status, 201, blocked, "创建高风险订单")["order"]["order_id"]
    status, resp = call(f"/api/orders/{bid}/pay", "POST", {})
    assert status == 403 and resp["error"]["code"] == "PAYMENT_RISK_BLOCKED", resp
    ok("支付拦截", resp["error"]["message"])

    # 3) 售后自动裁决
    status, o2 = call("/api/orders", "POST", {
        "buyer_id": buyer["party_id"], "seller_id": seller["party_id"],
        "amount_cents": 12000, "sku_desc": "退货订单",
    })
    oid2 = must(status, 201, o2, "创建退货订单")["order"]["order_id"]
    status, p2 = call(f"/api/orders/{oid2}/pay", "POST", {})
    must(status, 200, p2, "支付退货订单")
    status, d = call(f"/api/orders/{oid2}/dispute", "POST",
                     {"dtype": "no_reason_return", "claimant": "buyer",
                      "evidence": {"return_signed": True}})
    d = must(status, 201, d, "发起售后")
    assert d["auto"] and d["refund_cents"] == 12000, d
    ok("售后退款", d["note"])

    status, o3 = call("/api/orders", "POST", {
        "buyer_id": buyer["party_id"], "seller_id": seller["party_id"],
        "amount_cents": 30000, "sku_desc": "仲裁订单",
    })
    oid3 = must(status, 201, o3, "创建仲裁订单")["order"]["order_id"]
    status, p3 = call(f"/api/orders/{oid3}/pay", "POST", {})
    must(status, 200, p3, "支付仲裁订单")
    status, d3 = call(f"/api/orders/{oid3}/dispute", "POST",
                      {"dtype": "partial_damage", "claimant": "buyer",
                       "evidence": {"photos": []}})
    d3 = must(status, 201, d3, "证据不足转仲裁")
    assert d3["status"] == "arbitrating", d3
    status, arb = call(f"/api/disputes/{d3['dispute_id']}/arbitrate", "POST",
                       {"refund_cents": 10000, "release_cents": 20000,
                        "reviewer": "合规-张三", "note": "按 1/3 赔付"})
    arb = must(status, 200, arb, "人工仲裁")
    ok("人工仲裁", f"退款={arb['refund_cents']} 释放={arb['release_cents']}")

    # 4) 审计与一致性
    status, chain = call("/api/audit/verify")
    chain = must(status, 200, chain, "哈希链校验")
    assert chain["valid"], chain
    ok("哈希链校验", f"entries={chain['entries']}")

    status, anchor = call("/api/audit/anchor", "POST", {})
    anchor = must(status, 200, anchor, "锚定")
    if anchor.get("merkle_root"):
        ok("Merkle 锚定", anchor["anchor"]["tx_id"][:18] + "…")

    status, escrow_info = call(f"/api/escrow/{seller['party_id']}")
    escrow_info = must(status, 200, escrow_info, "担保账户")
    assert escrow_info["reconcile"]["consistent"], escrow_info["reconcile"]
    ok("担保账户对账", f"locked={escrow_info['account']['locked_cents']} "
                      f"settled={escrow_info['account']['settled_cents']}")

    status, metrics = call("/api/admin/metrics")
    metrics = must(status, 200, metrics, "指标")
    ok("业务指标", f"orders={metrics['orders']['total']} block_rate={metrics['risk']['block_rate']}")

    status, cons = call("/api/admin/consistency")
    cons = must(status, 200, cons, "一致性核对")
    assert cons["ledger_chain"]["valid"] and cons["escrow"]["all_consistent"], cons
    ok("一致性核对")

    status, html = call_raw("/console/index.html")
    assert status == 200 and "CBETS-B2C" in html, status
    ok("管理前端可访问", "/console/index.html")
    status, js = call_raw("/console/app.js")
    assert status == 200 and "loadOrders" in js, status
    ok("前端脚本可访问", "/console/app.js")

    print(f"\nHTTP 端到端全部通过：{len(steps)} 项")


if __name__ == "__main__":
    main()
