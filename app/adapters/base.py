"""适配层接口定义。

每个外部系统只暴露一个窄接口。接真实环境时，新建一个类实现同样的方法，
在 adapters/__init__.py 的工厂里按 CBETS_ADAPTER_MODE 切换，业务层代码不需要改。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LogisticsAdapter(ABC):
    @abstractmethod
    def create_shipment(self, *, order_id: str, carrier: str, tracking_no: str,
                        order_created_at: str) -> dict[str, Any]:
        """创建运单，返回 tracking_no / pickup_time / events。"""

    @abstractmethod
    def query_tracking(self, tracking_no: str) -> dict[str, Any]:
        """查询轨迹，返回 {status, events:[{time, status, location}]}。"""


class WalletAdapter(ABC):
    @abstractmethod
    def kyc_level(self, wallet_id: str) -> int:
        """返回实名等级：0 未实名，1 二类，2 三类及以上。"""

    @abstractmethod
    def debit(self, *, wallet_id: str, amount_cents: int, ref_no: str) -> dict[str, Any]:
        """从买家钱包扣款，返回 {ok, balance_cents, tx_id}。"""

    @abstractmethod
    def credit(self, *, wallet_id: str, amount_cents: int, ref_no: str) -> dict[str, Any]:
        """向收款方钱包入账（担保释放或退款），返回 {ok, tx_id}。"""


class CBETSAdapter(ABC):
    """数币达（CBETS）跨境数字人民币收付接口。真实环境替换为银行网关 SDK。"""

    @abstractmethod
    def create_trade(self, *, order_id: str, amount_cents: int, currency: str,
                     payer_wallet: str, payee_wallet: str, idempotency_key: str,
                     memo: str = "") -> dict[str, Any]:
        """发起跨境支付，返回 {trade_no, status, settled_at}。status ∈ settled/failed。"""

    @abstractmethod
    def query_trade(self, trade_no: str) -> dict[str, Any]:
        """查询支付状态。"""

    @abstractmethod
    def refund(self, *, trade_no: str, amount_cents: int, idempotency_key: str,
               reason: str = "") -> dict[str, Any]:
        """按原交易退回资金，返回 {refund_no, status}。"""


class ChainAdapter(ABC):
    """上链存证接口。Mock 用本地哈希链，真实环境接 FISCO BCOS / 蚂蚁链 SDK。"""

    @abstractmethod
    def anchor(self, *, batch_id: str, merkle_root: str, from_seq: int, to_seq: int) -> dict[str, Any]:
        """把 Merkle 根写入链上，返回 {tx_id, block_height, chain}。"""


class CommerceAdapter(ABC):
    """电商平台侧接口：同步订单上下文、回写风控结论。"""

    @abstractmethod
    def order_context(self, order_id: str) -> dict[str, Any]:
        """返回平台侧订单快照（SKU、金额、下单时间、买家账号）。"""

    @abstractmethod
    def notify_decision(self, *, order_id: str, decision: str, reasons: list[str]) -> dict[str, Any]:
        """把风控结论回写平台，用于平台侧拦截或人工队列。"""
