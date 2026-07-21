"""crypto 半自动交易策略引擎 —— 需求3。

MoneyBill 把 Jason 人话编译成 `crypto_intel_engine/dsl.py` 的 `CryptoStrategySpec`（落
`CryptoStrategy` 表）；本包的后台引擎按 tick 读取已启用策略、用 `analyze_crypto_symbol`
评估条件、过成本闸+护栏+**5 条不可绕过的硬风控**：
- `paper` 模式：只把「本该下的单」记进 run 日志（干跑观察，不排单、不成交）。
- `live` 模式：**自动产决策 + 自动排一张待确认单**（`CryptoPendingOrder`）推给 Jason，
  Jason 逐笔点确认、确认时**再跑一次风控**才经 `execution.py` 真成交。

⛔ Jason 2026-07-21 拍板：**先做半自动、全自动不信任要先验证**。引擎**永不自动成交**，
CLAUDE.md 逐笔人工确认红线原样保留、一个字不动。交互式聊天工具 `place_crypto_order`
不受影响，A 股 automation 不受影响。执行/风控/资金/台账原语复用
`crypto_intel_engine/execution.py`（与聊天下单同一份，永不分叉）。
"""
