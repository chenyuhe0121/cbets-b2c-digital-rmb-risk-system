"""资金守卫回归测试（临时库，不碰 data/ 演示数据）。

覆盖两条已修复缺陷 + 周边边界：
- 担保期与人工复核真正约束放款；
- CBETS 适配器累计退款不得超过原交易额。

用法：python scripts/guard_tests.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_tmp = tempfile.mkdtemp(prefix="cbets_guard_")
os.environ["CBETS_DATA_DIR"] = _tmp
os.environ["CBETS_DB_PATH"] = os.path.join(_tmp, "guard.db")
os.environ["CBETS_MODEL_DIR"] = os.path.join(_tmp, "models")

from app.adapters import cbets  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.gateway import order_center, payment_router  # noqa: E402

PASSED: list[str] = []


def ok(name: str, detail: str = "") -> None:
    PASSED.append(name)
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def expect_reject(name: str, fn, code: str) -> None:
    try:
        fn()
    except ApiError as exc:
        assert exc.code == code, f"{name}: 期望 {code}，实际 {exc.code} ({exc.message})"
        ok(name, f"{exc.code}: {exc.message}")
        return
    raise AssertionError(f"{name}: 本应被拒绝，却执行成功")


def new_order(amount_cents: int = 29900) -> str:
    conn = connect()
    try:
        created = order_center.create_order(conn, buyer_id="BUY-DEMO-001", seller_id="SELL-DEMO-001",
                                            amount_cents=amount_cents, sku_desc="守卫测试商品")
        return created["order"]["order_id"]
    finally:
        conn.close()


def backdate_hold(order_id: str, hours: int = 1) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    conn = connect()
    try:
        conn.execute("UPDATE contracts SET hold_until=? WHERE order_id=?", (past, order_id))
        conn.commit()
    finally:
        conn.close()


def make_seller_review() -> None:
    conn = connect()
    try:
        profile = json.loads(conn.execute(
            "SELECT profile FROM parties WHERE party_id='SELL-DEMO-001'").fetchone()["profile"])
        profile["complaint_rate_30d"] = 0.30   # 命中 S01：复核而非拦截
        conn.execute("UPDATE parties SET profile=? WHERE party_id='SELL-DEMO-001'",
                     (json.dumps(profile, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    init_db()

    # —— 缺陷一：放款不受担保期约束 ——
    oid = new_order()
    payment_router.pay(order_id=oid)
    payment_router.ship(order_id=oid, carrier="燕文", tracking_no="YWG0001")
    expect_reject("未收货且担保期未到期，禁止放款",
                  lambda: payment_router.release(order_id=oid), "ESCROW_HOLD_ACTIVE")

    payment_router.receive(order_id=oid)
    released = payment_router.release(order_id=oid)
    assert released["gross_cents"] == 29900, released
    ok("买家确认收货后可立即放款", f"gross={released['gross_cents']} net={released['net_cents']}")

    oid2 = new_order()
    payment_router.pay(order_id=oid2)
    payment_router.ship(order_id=oid2, carrier="燕文", tracking_no="YWG0002")
    backdate_hold(oid2)
    expired_release = payment_router.release(order_id=oid2)
    ok("未收货但担保期届满，允许结算", f"gross={expired_release['gross_cents']}")

    # —— 复核单必须人工放款 ——
    make_seller_review()
    oid3 = new_order()
    paid = payment_router.pay(order_id=oid3)
    assert paid["risk"]["decision"] == "review", paid["risk"]
    assert paid["escrow_hold_hours"] == settings.review_hold_hours, paid["escrow_hold_hours"]
    ok("复核单支付放行并延长担保期", f"hold={paid['escrow_hold_hours']}h")
    payment_router.ship(order_id=oid3, carrier="燕文", tracking_no="YWG0003")
    payment_router.receive(order_id=oid3)
    expect_reject("复核单无人工复核人，禁止放款",
                  lambda: payment_router.release(order_id=oid3), "REVIEW_REQUIRED")
    manual = payment_router.release(order_id=oid3, reviewer="合规-测试")
    ok("复核单人工放款成功", f"gross={manual['gross_cents']}")

    # —— 缺陷二：CBETS 累计退款无上限 ——
    trade = cbets().create_trade(order_id="GUARD-CB", amount_cents=10000, currency="CNY",
                                 payer_wallet="W-BUY-001", payee_wallet="W-SELL-001",
                                 idempotency_key="guard-cb-1")
    first = cbets().refund(trade_no=trade["trade_no"], amount_cents=6000, idempotency_key="g-rf-1")
    assert first["total_refunded_cents"] == 6000 and first["status"] == "partial_refunded", first
    ok("部分退款累计记账", f"已退 {first['total_refunded_cents']} / 10000")
    expect_reject("累计退款超过原交易额被拒",
                  lambda: cbets().refund(trade_no=trade["trade_no"], amount_cents=10000,
                                         idempotency_key="g-rf-2"), "BAD_REQUEST")
    second = cbets().refund(trade_no=trade["trade_no"], amount_cents=4000, idempotency_key="g-rf-3")
    assert second["status"] == "refunded" and second["total_refunded_cents"] == 10000, second
    ok("补足剩余额度后交易转为已退款", f"累计 {second['total_refunded_cents']}")
    expect_reject("全额退款后再次退款被拒",
                  lambda: cbets().refund(trade_no=trade["trade_no"], amount_cents=1,
                                         idempotency_key="g-rf-4"), "ALREADY_REFUNDED")
    trade2 = cbets().create_trade(order_id="GUARD-CB2", amount_cents=5000, currency="CNY",
                                  payer_wallet="W-BUY-001", payee_wallet="W-SELL-001",
                                  idempotency_key="guard-cb-2")
    expect_reject("退款金额必须为正数",
                  lambda: cbets().refund(trade_no=trade2["trade_no"], amount_cents=0,
                                         idempotency_key="g-rf-5"), "BAD_REQUEST")

    print(f"\n全部通过：{len(PASSED)} 项。临时库 {settings.database_path}")


if __name__ == "__main__":
    main()
