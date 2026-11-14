"""售后纠纷处理：五类场景的自动核验规则 + 资金执行。

自动裁决只做"证据能验"的部分，验不了就转人工仲裁，不搞黑箱判定。
每类场景的举证要求写在 POLICIES 里，接口文档直接引用这份说明。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from ..adapters import cbets, logistics, wallet
from ..db import connect, now_iso, tx
from ..errors import bad_request, conflict, not_found
from . import escrow, state_machine

POLICIES = {
    "no_reason_return": {
        "label": "7天无理由全额退货",
        "claimant": "buyer",
        "evidence": ["return_tracking_no（退货物流单号，需已签收）"],
        "auto": "退货物流签收后全额退款",
    },
    "partial_damage": {
        "label": "部分货损/少发漏发",
        "claimant": "buyer",
        "evidence": ["photos（破损或数量不符照片，至少 1 张）", "damage_ratio（可选，0-1，缺省 0.3）"],
        "auto": "按 damage_ratio 部分退款，剩余释放给商户",
    },
    "fake_delivery": {
        "label": "卖家虚假发货/空包",
        "claimant": "buyer",
        "evidence": ["tracking_no（平台运单号）"],
        "auto": "无运单记录或揽收早于下单，全额退款并冻结商户",
    },
    "malicious_refund": {
        "label": "买家恶意退款/虚假拒付",
        "claimant": "seller",
        "evidence": ["tracking_no（原始运单）", "signed_receipt=true（签收证明）"],
        "auto": "签收证明成立，全额释放给商户并把买家标记为欺诈",
    },
    "logistics_loss": {
        "label": "跨境物流丢件/破损",
        "claimant": "logistics",
        "evidence": ["loss_confirmed=true", "insured（是否保价）"],
        "auto": "保价全额退款；未保价双方各承担一半",
    },
}


def _dispute_id() -> str:
    return "DP" + uuid.uuid4().hex[:14].upper()


def _wallet_of(conn: sqlite3.Connection, party_id: str) -> str:
    row = conn.execute("SELECT wallet_id FROM parties WHERE party_id=?", (party_id,)).fetchone()
    if not row:
        raise not_found(f"交易主体 {party_id}")
    return row["wallet_id"]


def _remaining(contract: dict) -> int:
    return contract["locked_cents"] - contract["released_cents"] - contract["refunded_cents"]


def _parse_iso(text: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(conn: sqlite3.Connection, *, dtype: str, order, evidence: dict,
             contract: dict) -> dict:
    """返回 {auto: bool, refund_cents, release_cents, note}。"""
    remaining = _remaining(contract)
    amount = int(order["amount_cents"])

    if dtype == "no_reason_return":
        if evidence.get("return_signed"):
            return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                    "note": "退货签收凭证已上传，全额退款"}
        tracking = evidence.get("return_tracking_no")
        if not tracking:
            return {"auto": False, "reason": "缺少退货物流单号或退货签收凭证"}
        try:
            track = logistics().query_tracking(tracking)
        except Exception:
            return {"auto": False, "reason": "退货物流单号查不到轨迹，需人工核验"}
        if track["status"] != "delivered":
            return {"auto": False, "reason": "退货物流尚未签收"}
        return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                "note": f"退货已签收（{tracking}），全额退款"}

    if dtype == "partial_damage":
        photos = evidence.get("photos") or []
        if not photos:
            return {"auto": False, "reason": "缺少破损/数量不符照片"}
        ratio = float(evidence.get("damage_ratio", 0.3))
        if not 0 < ratio <= 1:
            return {"auto": False, "reason": "damage_ratio 需在 (0,1] 区间"}
        refund = int(round(remaining * ratio))
        if refund >= remaining:
            return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                    "note": "货损比例达 100%，全额退款"}
        return {"auto": True, "refund_cents": refund, "release_cents": remaining - refund,
                "note": f"按货损比例 {ratio:.0%} 退款 {refund} 分，剩余释放"}

    if dtype == "fake_delivery":
        ship = conn.execute("SELECT * FROM shipments WHERE order_id=?", (order["order_id"],)).fetchone()
        if not ship:
            return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                    "note": "无有效揽收记录，判定虚假发货，全额退款并冻结商户", "freeze_seller": True}
        pickup = ship["pickup_time"]
        created = order["created_at"]
        p_dt, c_dt = _parse_iso(pickup), _parse_iso(created)
        if p_dt and c_dt and p_dt < c_dt:
            return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                    "note": "揽收时间早于下单时间，判定刷单，全额退款并冻结商户",
                    "freeze_seller": True}
        return {"auto": False, "reason": "存在运单记录，空包主张需人工核验重量与轨迹"}

    if dtype == "malicious_refund":
        tracking = evidence.get("tracking_no")
        if not tracking or not evidence.get("signed_receipt"):
            return {"auto": False, "reason": "缺少运单号或签收证明"}
        try:
            track = logistics().query_tracking(tracking)
        except Exception:
            return {"auto": False, "reason": "运单查询失败"}
        if track["status"] != "delivered":
            return {"auto": False, "reason": "物流未显示签收，卖家主张不成立"}
        return {"auto": True, "refund_cents": 0, "release_cents": remaining,
                "note": "卖家已提供签收证明，资金释放给商户", "mark_buyer_fraud": True}

    if dtype == "logistics_loss":
        if not evidence.get("loss_confirmed"):
            return {"auto": False, "reason": "物流方尚未确认丢件/破损"}
        if evidence.get("insured"):
            return {"auto": True, "refund_cents": remaining, "release_cents": 0,
                    "note": "已保价，全额退款，赔付由物流方另行结算"}
        refund = remaining // 2
        return {"auto": True, "refund_cents": refund, "release_cents": remaining - refund,
                "note": "未保价，双方各承担一半"}

    raise bad_request(f"未知售后场景 {dtype}")


def _settle(conn_inner: sqlite3.Connection, *, order, dispute: dict, refund_cents: int,
            release_cents: int, note: str) -> dict:
    """在事务内完成账本与状态变更，返回需要在事务外执行的钱包操作。"""
    c = conn_inner
    contract = state_machine.get_contract(c, order["order_id"])
    remaining = _remaining(contract)
    if refund_cents + release_cents != remaining:
        raise bad_request("结算金额必须等于当前锁定的剩余金额",
                          {"remaining_cents": remaining, "refund_cents": refund_cents,
                           "release_cents": release_cents})
    buyer_wallet = _wallet_of(c, order["buyer_id"])
    seller_wallet = _wallet_of(c, order["seller_id"])
    ops: list[dict] = []
    summary: dict = {"refund_cents": refund_cents, "release_cents": release_cents}

    if refund_cents > 0:
        escrow.refund_to_buyer(c, seller_id=order["seller_id"], order_id=order["order_id"],
                               amount_cents=refund_cents, ref_no=dispute["dispute_id"],
                               memo=f"售后退款：{POLICIES[dispute['dtype']]['label']}")
        ops.append({"kind": "credit", "wallet_id": buyer_wallet, "amount_cents": refund_cents,
                    "ref_no": f"{dispute['dispute_id']}-refund"})
    if release_cents > 0:
        info = escrow.release_to_seller(c, seller_id=order["seller_id"], order_id=order["order_id"],
                                        gross_cents=release_cents, ref_no=dispute["dispute_id"],
                                        memo=f"售后释放：{POLICIES[dispute['dtype']]['label']}")
        summary["fee_cents"] = info["fee_cents"]
        ops.append({"kind": "credit", "wallet_id": seller_wallet, "amount_cents": info["net_cents"],
                    "ref_no": f"{dispute['dispute_id']}-release"})
    if refund_cents > 0:
        trade = c.execute("SELECT trade_no FROM cbets_trades WHERE order_id=?",
                          (order["order_id"],)).fetchone()
        if trade:
            ops.append({"kind": "cbets_refund", "trade_no": trade["trade_no"],
                        "amount_cents": refund_cents, "ref_no": f"{dispute['dispute_id']}-cbets"})

    event = "refund" if refund_cents == remaining else (
        "partial_refund" if refund_cents > 0 else "release")
    state_machine.transition(c, order_id=order["order_id"], event=event)

    c.execute("UPDATE disputes SET status='resolved', decision=?, refund_cents=?, updated_at=? "
              "WHERE dispute_id=?",
              (json.dumps({"note": note, "refund_cents": refund_cents,
                           "release_cents": release_cents}, ensure_ascii=False),
               refund_cents, now_iso(), dispute["dispute_id"]))
    c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
              ("dispute.resolved", dispute["dispute_id"],
               json.dumps({"order_id": order["order_id"], "note": note}, ensure_ascii=False), now_iso()))
    return {"ops": ops, "summary": summary}


def _run_external(ops: list[dict]) -> list[dict]:
    results = []
    for op in ops:
        if op["kind"] == "credit":
            results.append(wallet().credit(wallet_id=op["wallet_id"], amount_cents=op["amount_cents"],
                                           ref_no=op["ref_no"]))
        elif op["kind"] == "cbets_refund":
            results.append(cbets().refund(trade_no=op["trade_no"], amount_cents=op["amount_cents"],
                                          idempotency_key=op["ref_no"], reason="售后退款"))
    return results


def open_dispute(*, order_id: str, dtype: str, claimant: str, evidence: dict) -> dict:
    if dtype not in POLICIES:
        raise bad_request(f"未知售后场景 {dtype}", {"supported": list(POLICIES)})
    conn = connect()
    try:
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not order:
            raise not_found(f"订单 {order_id}")
        contract = state_machine.get_contract(conn, order_id)
        if contract["status"] in state_machine.TERMINAL:
            raise conflict("CONTRACT_CLOSED", "合约已结清，不能再发起售后",
                           {"status": contract["status"]})
        dispute_id = _dispute_id()
        with tx(conn) as c:
            c.execute(
                "INSERT INTO disputes(dispute_id,order_id,dtype,claimant,status,evidence,decision,"
                "refund_cents,created_at,updated_at) VALUES(?,?,?,?,'open',?,'{}',0,?,?)",
                (dispute_id, order_id, dtype, claimant, json.dumps(evidence, ensure_ascii=False),
                 now_iso(), now_iso()),
            )
            state_machine.transition(c, order_id=order_id, event="open_dispute")
        verdict = classify(conn, dtype=dtype, order=order, evidence=evidence, contract=contract)

        if not verdict.get("auto"):
            with tx(conn) as c:
                c.execute("UPDATE disputes SET status=? , decision=?, updated_at=? WHERE dispute_id=?",
                          (state_machine.STATUS_ARBITRATION,
                           json.dumps({"auto": False, "reason": verdict["reason"]}, ensure_ascii=False),
                           now_iso(), dispute_id))
                state_machine.transition(c, order_id=order_id, event="arbitrate")
            return {"dispute_id": dispute_id, "status": "arbitrating", "auto": False,
                    "reason": verdict["reason"], "policies": POLICIES[dtype]}

        with tx(conn) as c:
            if verdict.get("freeze_seller"):
                escrow.freeze(c, seller_id=order["seller_id"], reason=verdict["note"])
            if verdict.get("mark_buyer_fraud"):
                _mark_buyer_fraud(c, order["buyer_id"])
            plan = _settle(c, order=order, dispute={"dispute_id": dispute_id, "dtype": dtype},
                           refund_cents=verdict["refund_cents"],
                           release_cents=verdict["release_cents"], note=verdict["note"])
        external = _run_external(plan["ops"])
        return {"dispute_id": dispute_id, "status": "resolved", "auto": True,
                "note": verdict["note"], **plan["summary"], "wallet_results": external,
                "policies": POLICIES[dtype]}
    finally:
        conn.close()


def _mark_buyer_fraud(conn: sqlite3.Connection, buyer_id: str) -> None:
    row = conn.execute("SELECT profile FROM parties WHERE party_id=?", (buyer_id,)).fetchone()
    if not row:
        return
    profile = json.loads(row["profile"] or "{}")
    profile["fraud_tag"] = 1
    profile["chargeback_cnt"] = int(profile.get("chargeback_cnt", 0)) + 1
    conn.execute("UPDATE parties SET profile=? WHERE party_id=?",
                 (json.dumps(profile, ensure_ascii=False), buyer_id))


def get_dispute(conn: sqlite3.Connection, dispute_id: str) -> dict:
    row = conn.execute("SELECT * FROM disputes WHERE dispute_id=?", (dispute_id,)).fetchone()
    if not row:
        raise not_found(f"纠纷 {dispute_id}")
    d = dict(row)
    for k in ("evidence", "decision"):
        d[k] = json.loads(d[k] or "{}")
    return d


def settle_for_arbitration(*, dispute_id: str, refund_cents: int, release_cents: int,
                           reviewer: str, note: str) -> dict:
    conn = connect()
    try:
        dispute = get_dispute(conn, dispute_id)
        if dispute["status"] != state_machine.STATUS_ARBITRATION:
            raise conflict("DISPUTE_NOT_IN_ARBITRATION", "该纠纷不在仲裁中",
                           {"status": dispute["status"]})
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (dispute["order_id"],)).fetchone()
        with tx(conn) as c:
            plan = _settle(c, order=order,
                           dispute=dispute, refund_cents=refund_cents,
                           release_cents=release_cents,
                           note=f"仲裁裁决（{reviewer}）：{note}")
            c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                      ("dispute.arbitrated", dispute_id,
                       json.dumps({"reviewer": reviewer, "note": note,
                                   "refund_cents": refund_cents,
                                   "release_cents": release_cents}, ensure_ascii=False), now_iso()))
        external = _run_external(plan["ops"])
        return {"dispute_id": dispute_id, "status": "resolved", "reviewer": reviewer,
                **plan["summary"], "wallet_results": external}
    finally:
        conn.close()
