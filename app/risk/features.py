"""特征构建：把订单、商户资料、钱包实名、物流实绩拼成两个模型要的向量。

买家 12 项、卖家 10 项，字段名与 synthetic.py 完全一致，改一处必须同步另一处。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import sqlite3

from ..adapters import wallet
from ..config import settings


def _profile(row) -> dict:
    try:
        return json.loads(row["profile"] or "{}")
    except json.JSONDecodeError:
        return {}


def _parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def buyer_features(conn: sqlite3.Connection, order) -> dict:
    buyer = conn.execute("SELECT * FROM parties WHERE party_id=?", (order["buyer_id"],)).fetchone()
    p = _profile(buyer) if buyer else {}
    created = _parse(order["created_at"])
    hour = created.astimezone(timezone.utc).hour if created else 12
    avg = float(p.get("avg_amount_cents") or 0) or float(order["amount_cents"])

    # 近 7 天本地已结退款次数（叠加平台侧历史）
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    local_refunds = conn.execute(
        "SELECT COUNT(*) AS n FROM disputes d JOIN orders o ON o.order_id=d.order_id "
        "WHERE o.buyer_id=? AND d.status IN ('auto_resolved','resolved') AND d.refund_cents>0 "
        "AND d.created_at>=?", (order["buyer_id"], since)
    ).fetchone()["n"]

    kyc = wallet().kyc_level(buyer["wallet_id"]) if buyer and buyer["wallet_id"] else 0
    return {
        "refund_7d": int(p.get("refund_7d", 0)) + int(local_refunds),
        "refund_rate_90d": float(p.get("refund_rate_90d", 0.0)),
        "chargeback_cnt": int(p.get("chargeback_cnt", 0)),
        "account_age_days": int(p.get("account_age_days", 0)),
        "real_name_level": int(kyc),
        "device_change": int(p.get("device_change", 0)),
        "ip_country_mismatch": int(p.get("ip_country_mismatch", 0)),
        "address_flag": int(p.get("address_flag", 0)),
        "amount_deviation_ratio": round(float(order["amount_cents"]) / max(avg, 1.0), 2),
        "hour": hour,
        "cross_border": int(p.get("cross_border", 1)),
        "fraud_tag": int(p.get("fraud_tag", 0)),
    }


def seller_features(conn: sqlite3.Connection, order) -> dict:
    seller = conn.execute("SELECT * FROM parties WHERE party_id=?", (order["seller_id"],)).fetchone()
    p = _profile(seller) if seller else {}
    feats = {
        "complaint_rate_30d": float(p.get("complaint_rate_30d", 0.0)),
        "fake_delivery_cnt": int(p.get("fake_delivery_cnt", 0)),
        "logistics_offset_hours": float(p.get("logistics_offset_hours", 12.0)),
        "shop_age_days": int(p.get("shop_age_days", 0)),
        "penalty_cnt": int(p.get("penalty_cnt", 0)),
        "blacklist_flag": int(p.get("blacklist_flag", 0)),
        "listing_violation_flag": int(p.get("listing_violation_flag", 0)),
        "order_growth_ratio": float(p.get("order_growth_ratio", 1.0)),
        "dispute_rate_30d": float(p.get("dispute_rate_30d", 0.0)),
        "avg_ship_hours": float(p.get("avg_ship_hours", 24.0)),
    }
    # 有运单时用实测揽收时间覆盖画像值，这是"事后"识别虚假发货的依据
    ship = conn.execute("SELECT pickup_time FROM shipments WHERE order_id=?",
                        (order["order_id"],)).fetchone()
    if ship:
        created, pickup = _parse(order["created_at"]), _parse(ship["pickup_time"])
        if created and pickup:
            feats["logistics_offset_hours"] = round((pickup - created).total_seconds() / 3600, 2)
    return feats
