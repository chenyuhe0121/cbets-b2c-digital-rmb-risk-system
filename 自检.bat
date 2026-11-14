@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  CBETS-B2C 工程自检（不碰你的演示数据）
echo ==========================================
echo.
python scripts\smoke_local.py
echo.
python scripts\guard_tests.py
echo.
echo 上面如果显示「全部通过：16 项」和「全部通过：11 项」，
echo 说明下单、支付、风控、担保、放款闸门、退款、仲裁、哈希链、对账都正常。
echo.
pause