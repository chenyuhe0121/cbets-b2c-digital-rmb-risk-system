"""服务入口。

本地启动：python main.py
生产启动：uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
（SQLite + 单进程写事务；要横向扩容请按 README 的迁移说明换 PostgreSQL。）
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, contract, orders, risk
from app.config import settings
from app.db import connect, init_db
from app.errors import ApiError, api_error_handler
from app.risk.model import get_model

logging.basicConfig(level=settings.log_level,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cbets")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    init_db()
    for kind in ("buyer", "seller"):
        m = get_model(kind)
        log.info("模型就绪 %s version=%s auc=%s", kind, m.version, m.metrics.get("auc_valid"))
    log.info("数据库 %s，适配器模式 %s", settings.database_path, settings.adapter_mode)
    yield


app = FastAPI(
    title="CBETS-B2C 跨境数字人民币支付与风控系统",
    version="1.0.0",
    description="订单 → 双向欺诈评分 → CBETS 支付 → 担保子钱包 → 履约释放 / 售后仲裁 → 哈希链存证",
    lifespan=lifespan,
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders.router, prefix="/api/orders", tags=["订单与支付"])
app.include_router(risk.router, prefix="/api/risk", tags=["风控"])
app.include_router(contract.router, prefix="/api", tags=["合约与售后"])
app.include_router(admin.router, prefix="/api/admin", tags=["运营"])


@app.get("/healthz", tags=["运维"])
def healthz():
    conn = connect()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {"status": "ok", "version": app.version,
            "adapter_mode": settings.adapter_mode, "db": str(settings.database_path)}


frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/console", StaticFiles(directory=frontend_dir, html=True), name="console")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/console/index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
