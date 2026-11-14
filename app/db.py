"""SQLite 存储层：连接、事务、建表、种子数据。

设计约束（写死在 schema 里，避免业务层写错账）：
1. 所有金额字段用整数分，禁止浮点。
2. contracts 表约束 released + refunded <= locked，超额释放会被数据库直接拒绝。
3. ledger_entries 只追加，带 prev_hash/entry_hash，用于审计校验。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS parties (
    party_id    TEXT PRIMARY KEY,
    role        TEXT NOT NULL CHECK (role IN ('buyer','seller')),
    name        TEXT NOT NULL,
    profile     TEXT NOT NULL DEFAULT '{}',
    wallet_id   TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    buyer_id      TEXT NOT NULL REFERENCES parties(party_id),
    seller_id     TEXT NOT NULL REFERENCES parties(party_id),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    currency      TEXT NOT NULL DEFAULT 'CNY',
    sku_desc      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    review_flag   INTEGER NOT NULL DEFAULT 0,
    hold_until    TEXT,
    created_at    TEXT NOT NULL,
    paid_at       TEXT,
    shipped_at    TEXT,
    received_at   TEXT,
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    order_id        TEXT PRIMARY KEY REFERENCES orders(order_id),
    seller_id       TEXT NOT NULL,
    locked_cents    INTEGER NOT NULL CHECK (locked_cents > 0),
    released_cents  INTEGER NOT NULL DEFAULT 0 CHECK (released_cents >= 0),
    refunded_cents  INTEGER NOT NULL DEFAULT 0 CHECK (refunded_cents >= 0),
    status          TEXT NOT NULL,
    hold_until      TEXT,
    version         INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    CHECK (released_cents + refunded_cents <= locked_cents)
);

CREATE TABLE IF NOT EXISTS escrow_accounts (
    seller_id       TEXT PRIMARY KEY,
    currency        TEXT NOT NULL DEFAULT 'CNY',
    locked_cents    INTEGER NOT NULL DEFAULT 0 CHECK (locked_cents >= 0),
    settled_cents   INTEGER NOT NULL DEFAULT 0 CHECK (settled_cents >= 0),
    refunded_cents  INTEGER NOT NULL DEFAULT 0 CHECK (refunded_cents >= 0),
    frozen          INTEGER NOT NULL DEFAULT 0,
    frozen_reason   TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id       TEXT NOT NULL UNIQUE,
    seller_id      TEXT NOT NULL,
    order_id       TEXT,
    entry_type     TEXT NOT NULL,
    amount_cents   INTEGER NOT NULL,
    locked_after   INTEGER NOT NULL,
    settled_after  INTEGER NOT NULL,
    ref_no         TEXT,
    memo           TEXT,
    prev_hash      TEXT NOT NULL,
    entry_hash     TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
-- 同一订单同一类资金动作只允许一条，从数据库层杜绝重复释放/重复退款
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_order_type
    ON ledger_entries(order_id, entry_type) WHERE order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS cbets_trades (
    trade_no        TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    amount_cents    INTEGER NOT NULL,
    refunded_cents  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    raw             TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_tokens (
    token_hash  TEXT PRIMARY KEY,
    buyer_id    TEXT NOT NULL,
    wallet_id   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

-- Mock 钱包/物流在本地保存一份外部系统的镜像，接真实接口后这两张表停用。
CREATE TABLE IF NOT EXISTS wallet_balances (
    wallet_id     TEXT PRIMARY KEY,
    balance_cents INTEGER NOT NULL DEFAULT 0,
    kyc_level     INTEGER NOT NULL DEFAULT 1,
    updated_at    TEXT NOT NULL
);

-- 钱包操作的幂等记录：同一 ref_no + 类型只扣/加一次，重试安全。
CREATE TABLE IF NOT EXISTS wallet_txns (
    tx_id       TEXT PRIMARY KEY,
    ref_no      TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('debit','credit')),
    wallet_id   TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_idem ON wallet_txns(ref_no, op);


CREATE TABLE IF NOT EXISTS shipments (
    order_id      TEXT PRIMARY KEY REFERENCES orders(order_id),
    tracking_no   TEXT NOT NULL,
    carrier       TEXT NOT NULL,
    pickup_time   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'in_transit',
    events        TEXT NOT NULL DEFAULT '[]',
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id       TEXT NOT NULL,
    stage          TEXT NOT NULL,
    buyer_prob     REAL NOT NULL DEFAULT 0,
    seller_prob    REAL NOT NULL DEFAULT 0,
    rule_action    TEXT NOT NULL DEFAULT 'pass',
    hit_rules      TEXT NOT NULL DEFAULT '[]',
    contributions  TEXT NOT NULL DEFAULT '[]',
    final_score    REAL NOT NULL DEFAULT 0,
    final_level    TEXT NOT NULL DEFAULT 'low',
    decision       TEXT NOT NULL DEFAULT 'pass',
    reasons        TEXT NOT NULL DEFAULT '[]',
    model_version  TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_order ON risk_records(order_id);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id      TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    dtype           TEXT NOT NULL,
    claimant        TEXT NOT NULL,
    status          TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '{}',
    decision        TEXT NOT NULL DEFAULT '{}',
    refund_cents    INTEGER NOT NULL DEFAULT 0 CHECK (refund_cents >= 0),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispute_order ON disputes(order_id);

CREATE TABLE IF NOT EXISTS rules (
    rule_id      TEXT PRIMARY KEY,
    scope        TEXT NOT NULL CHECK (scope IN ('buyer','seller')),
    name         TEXT NOT NULL,
    field        TEXT NOT NULL,
    op           TEXT NOT NULL,
    threshold    TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('block','review')),
    weight       REAL NOT NULL DEFAULT 0.35,
    enabled      INTEGER NOT NULL DEFAULT 1,
    description  TEXT NOT NULL DEFAULT '',
    extra        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic         TEXT NOT NULL,
    aggregate_id  TEXT,
    payload       TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);

CREATE TABLE IF NOT EXISTS anchors (
    anchor_id      TEXT PRIMARY KEY,
    merkle_root    TEXT NOT NULL,
    entry_from_seq INTEGER NOT NULL,
    entry_to_seq   INTEGER NOT NULL,
    chain          TEXT NOT NULL,
    tx_id          TEXT,
    created_at     TEXT NOT NULL
);
"""


def now_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def iso_plus_hours(hours: int) -> str:
    return now_iso(datetime.now(timezone.utc) + timedelta(hours=hours))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    settings.ensure_dirs()
    path = str(db_path or settings.database_path)
    conn = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """显式事务。BEGIN IMMEDIATE 保证资金写入串行化，避免并发双花。"""
    own = conn is None
    c = conn or connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    finally:
        if own:
            c.close()


DEFAULT_RULES = [
    # ---- 买家 ----
    ("B01", "buyer", "买家7天内高频退款", "refund_7d", "gte", 5, "block", 0.85,
     "7 日内退款次数达到 5 次，按异常退款账户处理。"),
    ("B02", "buyer", "钱包未实名", "real_name_level", "eq", 0, "block", 0.9,
     "数字人民币钱包实名等级为 0，不具备支付条件。"),
    ("B03", "buyer", "凌晨大额交易", "hour", "between", [0, 5], "review", 0.4,
     "0-6 点且单笔金额达到 500 元的交易进入人工复核。", {"amount_gte_cents": 50000}),
    ("B04", "buyer", "历史欺诈标记", "fraud_tag", "eq", 1, "block", 0.95,
     "买家存在已确认的欺诈记录。"),
    ("B05", "buyer", "单笔金额偏离历史均值", "amount_deviation_ratio", "gte", 5.0, "review", 0.35,
     "单笔金额超过该买家历史均值 5 倍。"),
    ("B06", "buyer", "历史拒付", "chargeback_cnt", "gte", 1, "review", 0.3,
     "买家存在拒付记录。"),
    # ---- 卖家 ----
    ("S01", "seller", "近30天投诉率过高", "complaint_rate_30d", "gte", 0.25, "review", 0.45,
     "近 30 天投诉率超过 25%。"),
    ("S02", "seller", "虚假发货历史", "fake_delivery_cnt", "gte", 2, "block", 0.85,
     "已确认虚假发货达到 2 次。"),
    ("S03", "seller", "揽收时间早于下单", "logistics_offset_hours", "lt", 0, "block", 0.9,
     "物流揽收时间早于下单时间，属于典型刷单特征。"),
    ("S04", "seller", "商户黑名单", "blacklist_flag", "eq", 1, "block", 0.95,
     "商户在平台风险黑名单内。"),
    ("S05", "seller", "禁售或侵权商品", "listing_violation_flag", "eq", 1, "block", 0.9,
     "商品命中禁售或侵权规则。"),
    ("S06", "seller", "订单量异常突增", "order_growth_ratio", "gte", 3.0, "review", 0.35,
     "单日订单量相对近 7 日均值突增 3 倍以上。"),
]


DEMO_PARTIES = [
    {
        "party_id": "BUY-DEMO-001", "role": "buyer", "name": "演示买家·正常",
        "profile": {"real_name_level": 2, "account_age_days": 420, "avg_amount_cents": 25000,
                    "refund_7d": 0, "refund_rate_90d": 0.05, "chargeback_cnt": 0,
                    "device_change": 0, "ip_country_mismatch": 0, "address_flag": 0,
                    "fraud_tag": 0, "cross_border": 1},
        "wallet_id": "W-BUY-001",
    },
    {
        "party_id": "BUY-DEMO-002", "role": "buyer", "name": "演示买家·退款异常",
        "profile": {"real_name_level": 2, "account_age_days": 30, "avg_amount_cents": 12000,
                    "refund_7d": 6, "refund_rate_90d": 0.62, "chargeback_cnt": 2,
                    "device_change": 1, "ip_country_mismatch": 1, "address_flag": 1,
                    "fraud_tag": 0, "cross_border": 1},
        "wallet_id": "W-BUY-002",
    },
    {
        "party_id": "BUY-DEMO-003", "role": "buyer", "name": "演示买家·未实名",
        "profile": {"real_name_level": 0, "account_age_days": 3, "avg_amount_cents": 30000,
                    "refund_7d": 0, "refund_rate_90d": 0.0, "chargeback_cnt": 0,
                    "device_change": 1, "ip_country_mismatch": 0, "address_flag": 0,
                    "fraud_tag": 0, "cross_border": 1},
        "wallet_id": "W-BUY-003",
    },
    {
        "party_id": "SELL-DEMO-001", "role": "seller", "name": "演示商户·正常",
        "profile": {"shop_age_days": 900, "complaint_rate_30d": 0.02, "fake_delivery_cnt": 0,
                    "logistics_offset_hours": 6, "penalty_cnt": 0, "blacklist_flag": 0,
                    "listing_violation_flag": 0, "order_growth_ratio": 1.0,
                    "dispute_rate_30d": 0.01, "avg_ship_hours": 18},
        "wallet_id": "W-SELL-001",
    },
    {
        "party_id": "SELL-DEMO-002", "role": "seller", "name": "演示商户·履约异常",
        "profile": {"shop_age_days": 70, "complaint_rate_30d": 0.31, "fake_delivery_cnt": 2,
                    "logistics_offset_hours": -3, "penalty_cnt": 1, "blacklist_flag": 0,
                    "listing_violation_flag": 0, "order_growth_ratio": 3.6,
                    "dispute_rate_30d": 0.22, "avg_ship_hours": 90},
        "wallet_id": "W-SELL-002",
    },
]


def init_db(conn: sqlite3.Connection | None = None, seed: bool = True) -> None:
    settings.ensure_dirs()
    own = conn is None
    c = conn or connect()
    try:
        c.executescript(SCHEMA)
        _migrate(c)
        if seed:
            _seed_rules(c)
            _seed_parties(c)
    finally:
        if own:
            c.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """旧库的轻量迁移：只加列，不改数据。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(cbets_trades)")}
    if "refunded_cents" not in cols:
        conn.execute("ALTER TABLE cbets_trades ADD COLUMN refunded_cents INTEGER NOT NULL DEFAULT 0")


def _seed_rules(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()["n"]:
        return
    ts = now_iso()
    for rid, scope, name, field, op, threshold, action, weight, desc, *rest in DEFAULT_RULES:
        extra = rest[0] if rest else {}
        conn.execute(
            "INSERT OR IGNORE INTO rules(rule_id,scope,name,field,op,threshold,action,weight,enabled,description,extra)"
            " VALUES(?,?,?,?,?,?,?,?,1,?,?)",
            (rid, scope, name, field, op, json.dumps(threshold), action, weight, desc, json.dumps(extra)),
        )
    conn.execute(
        "INSERT INTO events(topic,aggregate_id,payload,created_at) VALUES(?,?,?,?)",
        ("rules.seeded", None, json.dumps({"count": len(DEFAULT_RULES)}), ts),
    )


def _seed_parties(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM parties").fetchone()["n"]:
        return
    ts = now_iso()
    for p in DEMO_PARTIES:
        conn.execute(
            "INSERT OR IGNORE INTO parties(party_id,role,name,profile,wallet_id,status,created_at)"
            " VALUES(?,?,?,?,?,'active',?)",
            (p["party_id"], p["role"], p["name"], json.dumps(p["profile"], ensure_ascii=False),
             p["wallet_id"], ts),
        )
        if p["role"] == "seller":
            conn.execute(
                "INSERT OR IGNORE INTO escrow_accounts(seller_id,currency,locked_cents,settled_cents,"
                "refunded_cents,frozen,updated_at) VALUES(?, 'CNY', 0, 0, 0, 0, ?)",
                (p["party_id"], ts),
            )
            initial_balance, kyc = 0, 2
        else:
            initial_balance, kyc = 5_000_000, int(p["profile"].get("real_name_level", 2))
        conn.execute(
            "INSERT OR IGNORE INTO wallet_balances(wallet_id,balance_cents,kyc_level,updated_at)"
            " VALUES(?,?,?,?)",
            (p["wallet_id"], initial_balance, kyc, ts),
        )
