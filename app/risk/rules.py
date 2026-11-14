"""规则引擎：规则存在数据库里，改规则不用改代码。

支持的操作符：gte / gt / lte / lt / eq / between / in。
rule.extra 可写 {"amount_gte_cents": 50000, "all": [[field, op, threshold], ...]} 表达复合条件。
字段缺失时该规则不命中（fail-open 到模型层），避免新商户没历史数据就全被拦。
"""
from __future__ import annotations

import json
from typing import Any

import sqlite3


def _cmp(op: str, value: Any, threshold: Any) -> bool:
    try:
        if op == "gte":
            return float(value) >= float(threshold)
        if op == "gt":
            return float(value) > float(threshold)
        if op == "lte":
            return float(value) <= float(threshold)
        if op == "lt":
            return float(value) < float(threshold)
        if op == "eq":
            return value == threshold or float(value) == float(threshold)
        if op == "between":
            lo, hi = threshold
            return float(value) >= float(lo) and float(value) <= float(hi)
        if op == "in":
            return value in threshold
    except (TypeError, ValueError):
        return False
    return False


def load_rules(conn: sqlite3.Connection, scope: str | None = None,
               include_disabled: bool = False) -> list[dict]:
    sql = "SELECT * FROM rules WHERE 1=1"
    args: list = []
    if not include_disabled:
        sql += " AND enabled=1"
    if scope:
        sql += " AND scope=?"
        args.append(scope)
    rows = conn.execute(sql + " ORDER BY rule_id", args).fetchall()
    return [
        {
            "rule_id": r["rule_id"], "scope": r["scope"], "name": r["name"], "field": r["field"],
            "op": r["op"], "threshold": json.loads(r["threshold"]), "action": r["action"],
            "weight": r["weight"], "extra": json.loads(r["extra"] or "{}"),
            "description": r["description"], "enabled": int(r["enabled"]),
        }
        for r in rows
    ]


def _extra_satisfied(extra: dict, features: dict, amount_cents: int) -> bool:
    if "amount_gte_cents" in extra and amount_cents < int(extra["amount_gte_cents"]):
        return False
    for field, op, threshold in extra.get("all", []):
        if field not in features:
            return False
        if not _cmp(op, features[field], threshold):
            return False
    return True


def evaluate(features: dict[str, Any], amount_cents: int, conn: sqlite3.Connection,
             scope: str) -> tuple[str, list[dict]]:
    """返回 (action, hits)；action ∈ block/review/pass。block 优先。"""
    action = "pass"
    hits: list[dict] = []
    for rule in load_rules(conn, scope):
        if rule["field"] not in features:
            continue
        if not _cmp(rule["op"], features[rule["field"]], rule["threshold"]):
            continue
        if not _extra_satisfied(rule["extra"], features, amount_cents):
            continue
        hits.append({
            "rule_id": rule["rule_id"], "scope": scope, "name": rule["name"],
            "action": rule["action"], "weight": rule["weight"],
            "field": rule["field"], "value": features[rule["field"]],
            "threshold": rule["threshold"],
        })
        if rule["action"] == "block":
            action = "block"
        elif action != "block":
            action = "review"
    return action, hits
