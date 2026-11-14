# CBETS-B2C 跨境数字人民币支付与风控系统

跨境电商 B2C 场景下的数字人民币（数币达 CBETS）收付 + 双向欺诈识别 + 担保子钱包 + 售后仲裁的可运行工程。
不依赖任何外部服务即可完整跑通：下单、风控评分、支付、担保锁定、发货收货、履约释放、五类售后裁决、人工仲裁、哈希链存证。

技术栈：FastAPI + SQLite（WAL）+ 原生 HTML/JS 控制台，**运行依赖只有 fastapi 和 uvicorn**。

## 跑起来

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

- 控制台：<http://localhost:8000/console/index.html>
- 接口文档（Swagger）：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/healthz>

首次启动会自动建库、写入 12 条风控规则、5 个演示主体，并训练两个评分模型（约 2 秒）。

自检（不污染正式库）：

```bash
python scripts/smoke_local.py                     # 业务层 16 项
python scripts/guard_tests.py                     # 资金守卫 11 项（担保期/人工复核/累计退款上限）
python scripts/e2e_http.py http://127.0.0.1:8000  # HTTP 层 17 项，需先启动服务
```

本机实测结果：三套自检全部通过（16 + 11 + 17）；模型版本 `lr-64a2796b` / `lr-d5442e9d`。

## 已验证 / 未验证

| 事项 | 状态 |
| --- | --- |
| 本地 Python 3.13 起服务、跑通全流程（业务层 16 项 + 资金守卫 11 项 + HTTP 17 项） | 已验证 |
| 放款闸门（未收货未到期拒放、复核单须人工）、CBETS 累计退款上限 | 已验证（`scripts/guard_tests.py`） |
| 哈希链校验、Merkle 根锚定、担保余额与合约对账 | 已验证 |
| 规则热配置、支付幂等、并发重复支付拦截 | 已验证 |
| Docker 镜像构建与 `docker-compose up` | **未验证**（开发机没装 Docker，配置按标准写法给出，首次构建请预留排查时间） |
| 真实 CBETS / 钱包 / 物流 / 平台接口 | **未实现**，全部为 Mock，接口与替换点已留好 |
| 模型真实业务准确率 | **不可声称**，训练数据是合成数据 |

## 目录与分层

```
cbets-b2c-risk-system/
├── app/
│   ├── adapters/          接入适配层（可替换）
│   │   ├── base.py              五个窄接口抽象
│   │   ├── cbets_adapter.py     数币达：create_trade / query_trade / refund
│   │   ├── wallet_adapter.py    钱包：kyc_level / debit / credit（幂等）
│   │   ├── logistics_adapter.py 物流：建运单 / 查轨迹 / 签收
│   │   ├── ecommerce_adapter.py 平台：订单上下文 / 风控结论回写
│   │   └── chain_adapter.py     上链锚定（Mock → 联盟链 SDK）
│   ├── gateway/           业务网关层：order_center / payment_router / tokenization
│   ├── risk/              双引擎风控：rules / features / model / decision
│   ├── contract/          条件合约层：escrow / state_machine / after_sales
│   ├── api/               HTTP 接口：orders / risk / contract / admin
│   ├── audit.py           账本哈希链 + Merkle 根
│   ├── db.py              schema、事务、种子数据
│   └── config.py          环境变量配置
├── frontend/              控制台（只调真实接口）
├── scripts/               smoke_local.py / guard_tests.py / e2e_http.py / reset_demo.py
├── data/                  SQLite 库与模型权重（不进 Git）
├── Dockerfile / docker-compose.yml / .env.example
└── main.py
```

与方案文档"六层架构"的对应关系：接入适配层 = `adapters/`，业务网关层 = `gateway/`，双引擎风控层 = `risk/`，条件合约层 = `contract/`，数据存储层 = `db.py` + SQLite，基础设施层 = Docker/配置/日志/`/docs`。

## 资金不变量（改代码前先看这一节）

1. **金额一律整数分**，库里没有浮点金额字段，接口字段名统一 `_cents`。
2. `contracts` 表带 CHECK：`released + refunded <= locked`，超额释放会被数据库拒绝。
3. `ledger_entries` 在 `(order_id, entry_type)` 上有唯一索引：同一订单的锁定、释放、退款各只能发生一次，重复扣款/双花在存储层被挡住。
4. 状态流转只能走 `state_machine.transition()`，用 `UPDATE ... WHERE status=? AND version=?` 做乐观锁，并发时第二个请求拿到 409。
5. 钱包扣款/入账以 `ref_no` 幂等（`wallet_txns` 唯一索引），网络重试不会重复记账。
6. 账本只追加，每条带 `prev_hash`/`entry_hash`；`/api/audit/verify` 重算全链；`/api/audit/anchor` 把一批条目的 Merkle 根写进 `anchors`。
7. `/api/escrow/{seller_id}` 的 `reconcile` 字段核对"账户锁定余额 = 未结清合约剩余锁定额之和"。
8. **放款闸门**（`payment_router.release`）：未发货不可放；买家未确认收货且 `hold_until` 未到期不可放（`ESCROW_HOLD_ACTIVE`）；`review_flag=1` 的复核单必须带 `reviewer` 人工放款（`REVIEW_REQUIRED`）。买家确认收货后可立即放款；担保期届满后即使未收货也可结算。
9. **CBETS 累计退款上限**：`cbets_trades.refunded_cents` 累记每次退款，`已退 + 本次 > 原交易额` 直接 400；全额退完后状态为 `refunded`，再退返回 `ALREADY_REFUNDED`。旧库启动时自动 `ALTER TABLE` 补这一列，不用手工迁移。

## 风控

**三个时机**：
- 事前 `GET /api/risk/preview?order_id=`：下单未付款时评分，只评分不动钱；
- 事中 `POST /api/orders/{id}/pay`：规则 + 模型联合决策，block 直接 403 并置订单 `risk_blocked`，review 放行但冻结期从 72 小时延长到 168 小时；
- 事后 `POST /api/orders/{id}/release`：先过放款闸门（见"资金不变量"第 8 条），再用实测物流时间重算商户特征，命中拦截规则则拒绝自动释放（可带 `reviewer` 人工复核）。

**规则引擎**：规则存 `rules` 表，支持 `gte/gt/lte/lt/eq/between/in` 与复合条件（`extra.all`、`extra.amount_gte_cents`），改阈值/启停走 `PATCH /api/risk/rules/{rule_id}`，不需要改代码和重启。

**模型**：买家 12 维、商户 10 维，纯 Python 逻辑回归，训练脚本内置（`scripts` 里没有单独脚本，`POST /api/risk/models/train` 或启动时自动训练），权重与标准化参数存 `data/models/*.json`。每次决策都会返回贡献最大的几个特征，解释可读。合成数据验证集 AUC 0.99 / 0.98——**这是合成数据上的数字，不是业务准确率**。

**融合**：`final = 1 - (1 - max(买家分, 商户分)) * (1 - 规则严重度)`；规则 block 直接短路；阈值可通过环境变量调整。

## 售后五场景

| 场景 `dtype` | 举证要求 | 自动裁决条件 | 资金动作 |
| --- | --- | --- | --- |
| `no_reason_return` | `return_signed=true` 或 `return_tracking_no` 已签收 | 凭证齐全 | 全额退回买家 |
| `partial_damage` | `photos`（≥1 张）、`damage_ratio` | 有照片且比例在 (0,1] | 按比例退款，剩余释放给商户 |
| `fake_delivery` | 运单号 | 无运单记录，或揽收时间早于下单 | 全额退款 + 冻结商户担保账户 |
| `malicious_refund` | `tracking_no`、`signed_receipt=true` | 物流显示已签收 | 全额释放给商户 + 买家打欺诈标 |
| `logistics_loss` | `loss_confirmed=true`、`insured` | 物流方确认 | 保价全退；未保价双方各半 |

验不了的一律转 `arbitrating`，由 `POST /api/disputes/{id}/arbitrate` 给结论，裁决金额必须等于当前剩余锁定额（退款 + 释放），不满足直接 400。

## 接口一览

| 方法 | 路径 | 作用 | 权限 |
| --- | --- | --- | --- |
| GET | `/healthz` | 健康检查 | 公开 |
| POST | `/api/orders` | 创建订单 | 公开 |
| GET | `/api/orders` | 订单列表 | 公开 |
| GET | `/api/orders/{order_id}` | 订单详情（合约/风控/账本/运单/纠纷） | 公开 |
| POST | `/api/orders/{order_id}/pay` | 支付（含事中风控） | 公开 |
| POST | `/api/orders/{order_id}/ship` | 发货建运单 | 公开 |
| POST | `/api/orders/{order_id}/receive` | 确认收货 | 公开 |
| POST | `/api/orders/{order_id}/release` | 释放担保资金（含事后风控） | 公开 |
| POST | `/api/orders/tokens/issue` | 签发支付标记 | 公开 |
| GET | `/api/risk/preview?order_id=` | 事前评分 | 公开 |
| GET | `/api/risk/records` | 风控记录 | 公开 |
| GET | `/api/risk/rules` | 规则库（含停用） | 公开 |
| PATCH | `/api/risk/rules/{rule_id}` | 改规则启停/动作/权重/阈值 | 管理员 |
| GET | `/api/risk/models` | 模型版本与指标 | 公开 |
| POST | `/api/risk/models/train?kind=buyer` | 重训练 | 管理员 |
| GET | `/api/risk/features?order_id=` | 特征与概率（排查用） | 公开 |
| GET | `/api/contract/{order_id}` | 合约状态与可执行事件 | 公开 |
| GET | `/api/escrow/{seller_id}` | 担保余额、流水、对账 | 公开 |
| POST | `/api/orders/{order_id}/dispute` | 发起售后 | 公开 |
| GET | `/api/disputes/{dispute_id}` | 纠纷详情 | 公开 |
| GET | `/api/policies` | 五类场景举证要求 | 公开 |
| POST | `/api/disputes/{dispute_id}/arbitrate` | 人工裁决 | 管理员 |
| GET | `/api/audit/verify` | 校验哈希链 | 公开 |
| GET | `/api/audit/entries` | 账本明细 | 公开 |
| POST | `/api/audit/anchor` | 锚定 Merkle 根 | 管理员 |
| GET | `/api/audit/anchors` | 已锚定批次 | 公开 |
| POST/GET | `/api/admin/parties` | 主体画像新增/列表 | 管理员 |
| POST | `/api/admin/wallets/topup` | 演示钱包充值 | 管理员 |
| GET | `/api/admin/metrics` | 业务指标 | 管理员 |
| GET | `/api/admin/consistency` | 账本/余额/入账一致性核对 | 管理员 |

管理员接口靠请求头 `X-Admin-Key`，对应环境变量 `CBETS_ADMIN_KEY`。**留空即无鉴权**，只能本地用。

## 接真实接口

1. 在 `app/adapters/` 下新建实现类，比如 `real/cbets_gateway.py`，实现 `CBETSAdapter` 的三个方法；
2. 在 `app/adapters/__init__.py` 的 `REGISTRY` 里注册，例如 `"real": {"cbets": RealCBETSGateway, ...}`；
3. 设置 `CBETS_ADAPTER_MODE=real`，`create_trade` 的 `idempotency_key` 传订单号前缀（`pay:{order_id}`），退款传纠纷号，保持现有幂等语义；
4. 需要向对方索取：数币达网关的接口文档与测试证书、钱包开放平台的实名等级查询口径、物流商轨迹接口、平台订单 OpenAPI 与风控回写字段。

业务层（`gateway/`、`contract/`、`risk/`）不需要任何改动。

## 上链

`anchors` 表存的是"某批账本条目的 Merkle 根 + 链上 tx_id"。接联盟链只改 `chain_adapter.py`：

```python
class FiscoChainAdapter(ChainAdapter):
    def anchor(self, *, batch_id, merkle_root, from_seq, to_seq):
        receipt = contract.functions.store(batch_id, merkle_root, from_seq, to_seq).transact()
        return {"tx_id": receipt["transactionHash"].hex(), "chain": "fisco", "block_height": receipt["blockNumber"]}
```

当前实现的哈希链与 Merkle 根是**可验证的防篡改账本**，不是区块链；两者别混着讲。

## 部署

```bash
cp .env.example .env      # 至少填 CBETS_ADMIN_KEY
docker compose up -d --build
curl http://<服务器IP>:8000/healthz
```

公网还需要：Nginx/Caddy 反代 + TLS、`CBETS_CORS_ORIGINS` 写具体域名、`CBETS_AUTO_TRAIN=0`（镜像构建时模型已随 data 卷保留）、定期备份 `data/`。

**进程模型**：SQLite 单写者，`--workers 1`。要横向扩容，按下面顺序换 PostgreSQL：`db.py` 的 schema 与 `connect/tx`、各模块的 `sqlite3.Row` 取值、`ON CONFLICT` 写法，然后把事务级别从 `BEGIN IMMEDIATE` 换成库级行锁。表结构与唯一索引可以直接迁移。

## 这个版本明确不做的事

- 真实 KYC/AML、外汇申报、监管报送：接口层没有，接真实环境前必须补；
- 多币种与 mBridge 场景：字段留了 `currency`，逻辑只按 CNY 走；
- 高可用：单机 SQLite，没有消息队列和读写分离；
- 模型的真实效果：合成数据训练的模型只能证明工程闭环，不能写进任何业务结论。

## 相关脚本

```bash
python scripts/smoke_local.py    # 业务层全流程（临时库，16 项）
python scripts/guard_tests.py    # 资金守卫回归（临时库，11 项）
python scripts/e2e_http.py       # HTTP 黑盒（17 项）
python scripts/reset_demo.py     # 清空演示库并重建（会删 data/cbets_b2c.db，需输入 yes 确认）
```
