"""离线全流程自检：不经过 HTTP，直接压业务层。

运行：python scripts/smoke_local.py
会用一个临时数据库，不影响 data/cbets_b2c.db。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP_DB = os.path.join(tempfile.gettempdir(), f"cbets_smoke_{uuid.uuid4().hex[:8]}.db")
os.environ["CBETS_DB_PATH"] = TMP_DB
os.environ.setdefault("CBETS_AUTO_TRAIN", "1")

from app.audit import verify_chain  # noqa: E402
from app.adapters import logistics  # noqa: E402
from app.contract import after_sales, escrow  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.gateway import order_center, payment_router  # noqa: E402
from app.risk.decision import assess  # noqa: E402

PASSED: list[str] = []


def ok(label: str, detail: str = "") -> None:
    PASSED.append(label)
    print(f"[PASS] {label}{(' — ' + detail) if detail else ''}")


def expect_error(fn, code: str) -> None:
    try:
        fn()
    except ApiError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}"
        ok(f"预期拒绝：{code}")
        return
    raise AssertionError(f"期望抛 {code}，但调用成功了")


def main() -> None:
    init_db()
    conn = connect()

    # 1. 正常路径
    o1 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                   amount_cents=29900, sku_desc="夏季连衣裙 2 件")
    preview = assess(conn, order_center.get_order(conn, o1["order"]["order_id"]), stage="pre")
    assert preview["decision"] in ("pass", "review"), preview["decision"]
    paid = payment_router.pay(order_id=o1["order"]["order_id"])
    assert paid["result"] == "paid", paid
    ok("正常订单支付", f"trades={paid['payment']['trade_no']} 风险={paid['risk']['decision']}")

    traded = order_center.get_order(conn, o1["order"]["order_id"])
    payment_router.ship(order_id=traded["order_id"], carrier="燕文", tracking_no="YW" + uuid.uuid4().hex[:10])
    payment_router.receive(order_id=traded["order_id"])
    rel = payment_router.release(order_id=traded["order_id"])
    assert rel["gross_cents"] == 29900 and rel["fee_cents"] == 179, rel
    detail = order_center.order_detail(conn, traded["order_id"])
    assert detail["contract"]["released_cents"] == 29900, detail["contract"]
    ok("履约释放", f"gross={rel['gross_cents']} fee={rel['fee_cents']} net={rel['net_cents']}")

    # 2. 买家高风险拦截
    o2 = order_center.create_order(conn, buyer_id="BUY-DEMO-002", seller_id="SELL-DEMO-001",
                                   amount_cents=18800, sku_desc="蓝牙耳机")
    expect_error(lambda: payment_router.pay(order_id=o2["order"]["order_id"]), "PAYMENT_RISK_BLOCKED")
    o2_now = order_center.get_order(conn, o2["order"]["order_id"])
    assert o2_now["status"] == "risk_blocked"
    ok("高风险买家被拦截", f"状态={o2_now['status']}")

    # 3. 未实名买家拦截
    o3 = order_center.create_order(conn, buyer_id="BUY-DEMO-003", seller_id="SELL-DEMO-001",
                                   amount_cents=9900, sku_desc="手机壳")
    expect_error(lambda: payment_router.pay(order_id=o3["order"]["order_id"]), "PAYMENT_RISK_BLOCKED")

    # 4. 商户风险拦截
    o4 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-002",
                                   amount_cents=15000, sku_desc="手表")
    expect_error(lambda: payment_router.pay(order_id=o4["order"]["order_id"]), "PAYMENT_RISK_BLOCKED")

    # 5. 7 天无理由退货：自动全额退款
    o5 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                   amount_cents=12000, sku_desc="运动鞋")
    payment_router.pay(order_id=o5["order"]["order_id"])
    payment_router.ship(order_id=o5["order"]["order_id"], carrier="燕文", tracking_no="RT" + uuid.uuid4().hex[:8])
    d5 = after_sales.open_dispute(order_id=o5["order"]["order_id"], dtype="no_reason_return",
                                  claimant="buyer", evidence={"return_signed": True})
    assert d5["auto"] and d5["refund_cents"] == 12000, d5
    detail5 = order_center.order_detail(conn, o5["order"]["order_id"])
    assert detail5["contract"]["refunded_cents"] == 12000, detail5["contract"]
    ok("7天无理由自动全额退款", d5["note"])

    # 6. 部分货损：按比例退款
    o6 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                   amount_cents=20000, sku_desc="陶瓷餐具")
    payment_router.pay(order_id=o6["order"]["order_id"])
    payment_router.ship(order_id=o6["order"]["order_id"], carrier="燕文", tracking_no="DM" + uuid.uuid4().hex[:8])
    d6 = after_sales.open_dispute(order_id=o6["order"]["order_id"], dtype="partial_damage",
                                  claimant="buyer",
                                  evidence={"photos": ["p1.jpg", "p2.jpg"], "damage_ratio": 0.3})
    assert d6["auto"] and d6["refund_cents"] == 6000, d6
    ok("部分货损按比例处理", f"退款={d6['refund_cents']} 释放={d6['release_cents']}")

    # 7. 虚假发货：无运单记录 → 全额退款 + 冻结商户
    o7 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                   amount_cents=8000, sku_desc="数据线")
    payment_router.pay(order_id=o7["order"]["order_id"])
    d7 = after_sales.open_dispute(order_id=o7["order"]["order_id"], dtype="fake_delivery",
                                  claimant="buyer", evidence={"tracking_no": "FAKE-001"})
    assert d7["auto"] and d7["refund_cents"] == 8000, d7
    acc = escrow.account(connect(), "SELL-DEMO-001")
    assert acc["frozen"] == 1, acc
    ok("虚假发货触发退款并冻结商户", d7["note"])

    # 8. 冻结后释放被拒
    o8 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                   amount_cents=5000, sku_desc="袜子")
    payment_router.pay(order_id=o8["order"]["order_id"])
    payment_router.ship(order_id=o8["order"]["order_id"], carrier="燕文", tracking_no="FR" + uuid.uuid4().hex[:8])
    payment_router.receive(order_id=o8["order"]["order_id"])  # 先确认收货，才能测到冻结守卫本身
    expect_error(lambda: payment_router.release(order_id=o8["order"]["order_id"]),
                 "SELLER_ACCOUNT_FROZEN")
    ok("冻结账户阻止资金释放")

    # 9. 物流丢件未保价：一半退款一半释放（先解冻再测）
    c = connect()
    with __import__("app.db", fromlist=["tx"]).tx(c) as t:
        escrow.unfreeze(t, seller_id="SELL-DEMO-001", reason="自检解冻")
    d9 = after_sales.open_dispute(order_id=o8["order"]["order_id"], dtype="logistics_loss",
                                  claimant="logistics",
                                  evidence={"loss_confirmed": True, "insured": False})
    assert d9["auto"] and d9["refund_cents"] == 2500 and d9["release_cents"] == 2500, d9
    ok("丢件未保价对半处理", d9["note"])

    # 10. 恶意退款：卖家举证签收 → 释放给商户 + 买家打标
    o10 = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                    amount_cents=6000, sku_desc="保温杯")
    payment_router.pay(order_id=o10["order"]["order_id"])
    tn = "SG" + uuid.uuid4().hex[:10]
    payment_router.ship(order_id=o10["order"]["order_id"], carrier="燕文", tracking_no=tn)
    logistics().mark_delivered(tn)
    d10 = after_sales.open_dispute(order_id=o10["order"]["order_id"], dtype="malicious_refund",
                                   claimant="seller", evidence={"tracking_no": tn, "signed_receipt": True})
    assert d10["auto"] and d10["release_cents"] == 6000, d10
    profile = conn.execute("SELECT profile FROM parties WHERE party_id='BUY-DEMO-001'").fetchone()
    assert '"fraud_tag": 1' in profile["profile"], profile["profile"]
    ok("恶意退款释放给商户并标记买家", d10["note"])

    # 11. 证据不足 → 转人工仲裁 → 裁决
    # 上一步刚把 BUY-DEMO-001 打了欺诈标，这里换一个干净买家，验证规则确实在生效
    import json
    from app.db import tx as db_tx
    from app.db import now_iso
    with db_tx(conn) as t:
        t.execute("INSERT OR IGNORE INTO parties(party_id,role,name,profile,wallet_id,status,created_at)"
                  " VALUES('BUY-TEST-CLEAN','buyer','干净买家',?, 'W-CLEAN', 'active', ?)",
                  (json.dumps({"real_name_level": 2, "account_age_days": 500,
                               "avg_amount_cents": 30000}), now_iso()))
        t.execute("INSERT OR IGNORE INTO wallet_balances(wallet_id,balance_cents,kyc_level,updated_at)"
                  " VALUES('W-CLEAN',5000000,2,?)", (now_iso(),))
    o11 = order_center.create_order(conn, buyer_id="BUY-TEST-CLEAN", seller_id="SELL-DEMO-001",
                                    amount_cents=30000, sku_desc="空气炸锅")
    payment_router.pay(order_id=o11["order"]["order_id"])
    d11 = after_sales.open_dispute(order_id=o11["order"]["order_id"], dtype="partial_damage",
                                   claimant="buyer", evidence={"photos": []})
    assert d11["status"] == "arbitrating", d11
    arb = after_sales.settle_for_arbitration(dispute_id=d11["dispute_id"], refund_cents=10000,
                                             release_cents=20000, reviewer="合规-张三",
                                             note="买家提供开箱视频，赔付 1/3")
    assert arb["refund_cents"] == 10000
    ok("证据不足转人工仲裁并裁决", arb["note"] if "note" in arb else "裁决完成")

    # 12. 账本与一致性
    check = verify_chain(connect())
    assert check["valid"], check
    ok("哈希链校验通过", f"entries={check['entries']}")
    for sid in ("SELL-DEMO-001", "SELL-DEMO-002"):
        rec = escrow.reconcile(connect(), sid)
        assert rec["consistent"], rec
    ok("担保余额与合约剩余锁定额一致")

    conn.close()
    print(f"\n全部通过：{len(PASSED)} 项。临时库 {TMP_DB}")


if __name__ == "__main__":
    main()
