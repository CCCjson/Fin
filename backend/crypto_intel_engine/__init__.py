"""加密货币情报引擎 —— 加密独有的分析层，股票没有。

两条腿（对应「排雷用于选币、择时用于进出」）：

- **择时辅助**（本轮先做）：`regime.py` BTC 200 日大势过滤（波段只在牛市做多的闸门）。
  技术信号本体复用 `analysis_engine`（零改吃 crypto OHLCV）；币安衍生品在
  `acquisition/markets/crypto_derivatives.py`。
- **排雷/选币**（波段+主流下先做薄，后续加厚）：链上/代币经济学/开发活跃/情绪采集器
  + 排雷打分。主流币不会归零/无解锁砸盘，故 v1 只做轻量选币闸门。

全部出网走 `acquisition/` 门面（免费源：币安/DefiLlama/CoinGecko/alternative.me/GitHub）。
"""
from crypto_intel_engine.cockpit import analyze_crypto_symbol
from crypto_intel_engine.regime import btc_regime, compute_regime
from crypto_intel_engine.scorer import market_context, screen_coin

__all__ = ["analyze_crypto_symbol", "btc_regime", "compute_regime",
           "market_context", "screen_coin"]
