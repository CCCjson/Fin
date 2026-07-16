"""
交易引擎配置

凭证一律从环境变量读取（.env），不得硬编码进仓库。
"""
# 风险控制配置（对标 CLAUDE.md 风控要求）
RISK_CONFIG = {
    # 单个品种最大持仓比例 ≤ 总资金 20%
    "max_position_pct": 0.20,

    # 总仓位不超过 80%，保留 20% 现金
    "max_total_position_pct": 0.80,

    # 单日最大亏损 ≤ 总资金 3%（动态计算）
    "max_daily_loss_pct": 0.03,

    # 止损止盈
    "stop_loss_pct": 0.05,   # 每笔交易必须设置止损 -5%
    "take_profit_pct": 0.15,  # 止盈 15%

    # 连续亏损 3 次后暂停交易 1 天
    "max_consecutive_losses": 3,
    "consecutive_loss_pause_days": 1,
}
