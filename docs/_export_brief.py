"""把运行中服务的真实数据导出成 JSON，供汇报页使用（只读，不写库）。"""
from __future__ import annotations

import json
import os
import urllib.request

BASE = os.environ.get("CBETS_BASE_URL", "http://127.0.0.1:8000")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brief-data.json")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


data = {
    "metrics": get("/api/admin/metrics"),
    "audit": get("/api/audit/verify"),
    "orders": get("/api/orders"),
    "rules": get("/api/risk/rules"),
    "models": get("/api/risk/models"),
    "escrow": get("/api/escrow/SELL-DEMO-001"),
    "consistency": get("/api/admin/consistency"),
    "health": get("/healthz"),
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("written", OUT)
print(json.dumps(data["metrics"], ensure_ascii=False))
print(json.dumps(data["audit"], ensure_ascii=False))
print(json.dumps(data["models"], ensure_ascii=False)[:600])
print("rules:", len(data["rules"].get("rules", data["rules"])))
print("orders:", [o["order_id"] + "/" + o["status"] for o in data["orders"]["items"]])
