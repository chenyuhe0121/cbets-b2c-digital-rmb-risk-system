"""运营与运维接口：主体管理、额度充值、业务指标、一致性核对。"""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..audit import verify_chain
from ..config import settings
from ..contract import escrow
from ..db import now_iso, tx
from ..errors import bad_request, not_found
from ..risk.model import get_model
from .deps import get_conn, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class PartyIn(BaseModel):
    party_id: str
    role: str = Field(pattern="^(buyer|seller)$")
    name: str
    profile: dict = Field(default_factory=dict)
    wallet_id: str | None = None


class TopUpIn(BaseModel):
    wallet_id: str
    amount_cents: int = Field(gt=0)


class FlagIn(BaseModel):
    field: str = Field(description="fraud_tag / chargeback_cnt / refund_7d / address_flag / "
                                   "device_change / ip_country_mismatch")
    value: int = Field(ge=0)


@router.post("/parties", status_code=201, summary="新增/更新交易主体画像")
def upsert_party(body: PartyIn, conn: sqlite3.Connection = Depends(get_conn)):
    wallet = body.wallet_id or ("W-" + body.party_id)
    existing = conn.execute("SELECT party_id FROM parties WHERE party_id=?", (body.party_id,)).fetchone()
    with tx(conn) as c:
        if existing:
            c.execute("UPDATE parties SET role=?,name=?,profile=?,wallet_id=? WHERE party_id=?",
                      (body.role, body.name, json.dumps(body.profile, ensure_ascii=False),
                       wallet, body.party_id))
        else:
            c.execute("INSERT INTO parties(party_id,role,name,profile,wallet_id,status,created_at)"
                      " VALUES(?,?,?,?,?,'active',?)",
                      (body.party_id, body.role, body.name,
                       json.dumps(body.profile, ensure_ascii=False), wallet, now_iso()))
            if body.role == "seller":
                c.execute("INSERT OR IGNORE INTO escrow_accounts(seller_id,currency,locked_cents,"
                          "settled_cents,refunded_cents,frozen,updated_at)"
                          " VALUES(?, 'CNY', 0, 0, 0, 0, ?)", (body.party_id, now_iso()))
        kyc = int(body.profile.get("real_name_level", 2)) if body.role == "buyer" else 2
        c.execute("INSERT OR IGNORE INTO wallet_balances(wallet_id,balance_cents,kyc_level,updated_at)"
                  " VALUES(?,?,?,?)",
                  (wallet, 5_000_000 if body.role == "buyer" else 0, kyc, now_iso()))
    return {"party_id": body.party_id, "wallet_id": wallet}


@router.get("/parties", summary="主体列表")
def list_parties(conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM parties ORDER BY role, party_id").fetchall()
    return {"items": [{**dict(r), "profile": json.loads(r["profile"])} for r in rows]}


@router.post("/wallets/topup", summary="给演示钱包充值（仅 Mock 环境）")
def topup(body: TopUpIn, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT wallet_id FROM wallet_balances WHERE wallet_id=?",
                       (body.wallet_id,)).fetchone()
    if not row:
        raise not_found(f"钱包 {body.wallet_id}")
    with tx(conn) as c:
        c.execute("UPDATE wallet_balances SET balance_cents=balance_cents+?, updated_at=? "
                  "WHERE wallet_id=?", (body.amount_cents, now_iso(), body.wallet_id))
        c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                  ("wallet.topup", body.wallet_id,
                   json.dumps({"amount_cents": body.amount_cents}), now_iso()))
    new = conn.execute("SELECT balance_cents FROM wallet_balances WHERE wallet_id=?",
                       (body.wallet_id,)).fetchone()["balance_cents"]
    return {"wallet_id": body.wallet_id, "balance_cents": new}


@router.post("/parties/{party_id}/flags", summary="平台治理：复核并清除/设置主体风控标记")
def set_party_flag(party_id: str, body: FlagIn, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT profile FROM parties WHERE party_id=?", (party_id,)).fetchone()
    if not row:
        raise not_found(f"交易主体 {party_id}")
    profile = json.loads(row["profile"] or "{}")
    profile[body.field] = body.value
    with tx(conn) as c:
        c.execute("UPDATE parties SET profile=? WHERE party_id=?",
                  (json.dumps(profile, ensure_ascii=False), party_id))
        c.execute("INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
                  ("party.flag_set", party_id,
                   json.dumps({"field": body.field, "value": body.value}, ensure_ascii=False),
                   now_iso()))
    return {"party_id": party_id, "field": body.field, "value": body.value, "profile": profile}


@router.get("/metrics", summary="业务指标：转化、拦截率、纠纷率、账本健康度")
def metrics(conn: sqlite3.Connection = Depends(get_conn)):
    orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    by_status = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM orders GROUP BY status").fetchall()}
    paid = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE paid_at IS NOT NULL").fetchone()["n"]
    blocked = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE status='risk_blocked'").fetchone()["n"]
    gmv = conn.execute("SELECT COALESCE(SUM(amount_cents),0) AS v FROM orders WHERE paid_at IS NOT NULL"
                       ).fetchone()["v"]
    released = conn.execute("SELECT COALESCE(SUM(released_cents),0) AS v FROM contracts").fetchone()["v"]
    refunded = conn.execute("SELECT COALESCE(SUM(refunded_cents),0) AS v FROM contracts").fetchone()["v"]
    disputes = conn.execute("SELECT COUNT(*) AS n FROM disputes").fetchone()["n"]
    arbitrating = conn.execute(
        "SELECT COUNT(*) AS n FROM disputes WHERE status IN ('arbitration','arbitrating')"
    ).fetchone()["n"]
    risk = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(AVG(final_score),0) AS avg_score FROM risk_records"
    ).fetchone()
    decisions = {r["decision"]: r["n"] for r in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM risk_records GROUP BY decision").fetchall()}
    return {
        "orders": {"total": orders, "by_status": by_status, "paid": paid, "risk_blocked": blocked},
        "money": {"gmv_cents": gmv, "released_gross_cents": released, "refunded_cents": refunded,
                  "escrow_locked_cents": conn.execute(
                      "SELECT COALESCE(SUM(locked_cents),0) AS v FROM escrow_accounts").fetchone()["v"],
                  "platform_fee_bps": settings.platform_fee_bps},
        "risk": {"records": risk["n"], "avg_score": round(risk["avg_score"], 4),
                 "decisions": decisions,
                 "block_rate": round(decisions.get("block", 0) / risk["n"], 4) if risk["n"] else 0},
        "after_sales": {"disputes": disputes, "arbitrating": arbitrating},
        "models": {"buyer": get_model("buyer").version, "seller": get_model("seller").version},
        "ledger": verify_chain(conn),
    }


@router.get("/consistency", summary="一致性核对：账本链、担保余额、释放/退款是否已入账")
def consistency(conn: sqlite3.Connection = Depends(get_conn)):
    chain_check = verify_chain(conn)
    escrow_checks = [escrow.reconcile(conn, r["seller_id"]) for r in
                     conn.execute("SELECT seller_id FROM escrow_accounts").fetchall()]
    missing_release = []
    for c in conn.execute("SELECT order_id, seller_id, locked_cents, released_cents FROM contracts "
                          "WHERE released_cents>0").fetchall():
        hit = conn.execute("SELECT 1 FROM wallet_txns WHERE ref_no=? AND op='credit'",
                           (f"release:{c['order_id']}",)).fetchone()
        if not hit and c["released_cents"] > 0:
            # 售后场景的释放走纠纷 ref，这里只核查正常履约释放
            also = conn.execute("SELECT 1 FROM wallet_txns WHERE ref_no LIKE ? AND op='credit'",
                                (f"%{c['order_id']}%",)).fetchone()
            if not also:
                missing_release.append(c["order_id"])
    missing_refund = []
    for d in conn.execute("SELECT dispute_id, order_id, refund_cents FROM disputes "
                          "WHERE refund_cents>0").fetchall():
        hit = conn.execute("SELECT 1 FROM wallet_txns WHERE ref_no=? AND op='credit'",
                           (f"{d['dispute_id']}-refund",)).fetchone()
        if not hit:
            missing_refund.append({"dispute_id": d["dispute_id"], "order_id": d["order_id"]})
    return {
        "ledger_chain": chain_check,
        "escrow": {"checks": escrow_checks,
                   "all_consistent": all(e["consistent"] for e in escrow_checks)},
        "wallet_credits": {"missing_release": missing_release, "missing_refund": missing_refund},
    }
