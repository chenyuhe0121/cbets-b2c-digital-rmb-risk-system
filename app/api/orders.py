"""订单与支付接口。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..gateway import order_center, payment_router, tokenization
from .deps import get_conn

router = APIRouter()


class OrderIn(BaseModel):
    buyer_id: str
    seller_id: str
    amount_cents: int = Field(gt=0, description="订单金额，单位：分")
    sku_desc: str = ""
    currency: str = "CNY"
    order_id: str | None = None


class PayIn(BaseModel):
    token: str | None = Field(default=None, description="支付标记，优先于 wallet_id")
    wallet_id: str | None = None


class ShipIn(BaseModel):
    carrier: str = Field(default="燕文", description="物流商")
    tracking_no: str


class ReleaseIn(BaseModel):
    reviewer: str | None = Field(default=None, description="人工复核人；事后风控拦截时必填")


class TokenIn(BaseModel):
    buyer_id: str


@router.post("", status_code=201, summary="创建订单")
def create_order(body: OrderIn, conn: sqlite3.Connection = Depends(get_conn)):
    return order_center.create_order(
        conn, buyer_id=body.buyer_id, seller_id=body.seller_id,
        amount_cents=body.amount_cents, sku_desc=body.sku_desc,
        currency=body.currency, order_id=body.order_id,
    )


@router.get("", summary="订单列表")
def list_orders(limit: int = Query(50, le=200), seller_id: str | None = None,
                buyer_id: str | None = None, conn: sqlite3.Connection = Depends(get_conn)):
    return {"items": order_center.list_orders(conn, limit=limit, seller_id=seller_id,
                                              buyer_id=buyer_id)}


@router.get("/{order_id}", summary="订单详情：合约、风控、账本、物流、纠纷")
def order_detail(order_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return order_center.order_detail(conn, order_id)


@router.post("/{order_id}/pay", summary="发起支付（含事中风控）")
def pay(order_id: str, body: PayIn | None = None):
    body = body or PayIn()
    return payment_router.pay(order_id=order_id, token=body.token, wallet_id=body.wallet_id)


@router.post("/{order_id}/ship", summary="发货并生成运单")
def ship(order_id: str, body: ShipIn):
    return payment_router.ship(order_id=order_id, carrier=body.carrier,
                               tracking_no=body.tracking_no)


@router.post("/{order_id}/receive", summary="确认收货")
def receive(order_id: str):
    return payment_router.receive(order_id=order_id)


@router.post("/{order_id}/release", summary="释放担保资金（含事后风控）")
def release(order_id: str, body: ReleaseIn | None = None):
    body = body or ReleaseIn()
    return payment_router.release(order_id=order_id, reviewer=body.reviewer)


@router.post("/tokens/issue", status_code=201, summary="签发支付标记")
def issue_token(body: TokenIn, conn: sqlite3.Connection = Depends(get_conn)):
    return tokenization.issue_token(conn, buyer_id=body.buyer_id)
