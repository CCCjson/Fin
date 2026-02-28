"""
交易引擎配置
"""

# OpenCTP 7x24 测试环境配置
OPENCTP_CONFIG = {
    # 经纪商配置
    "broker_id": "9999",  # SimNow 经纪商代码
    "user_id": "254987",  # 你的账号
    "password": "Chenjinsheng0828!",  # SimNow 交易密码

    # 认证信息
    "app_id": "simnow_client_test",  # 应用ID
    "auth_code": "0000000000000000",  # 授权码

    # 服务器地址（7x24 测试环境 - 电信线路）
    "md_address": "tcp://180.168.146.187:10131",  # 行情服务器
    "td_address": "tcp://180.168.146.187:10130",  # 交易服务器
}

# 风险控制配置（对标 CLAUDE.md 风控要求）
RISK_CONFIG = {
    # 单个品种最大持仓比例 ≤ 总资金 20%
    "max_position_pct": 0.20,

    # 总仓位不超过 80%，保留 20% 现金
    "max_total_position_pct": 0.80,

    # 单日最大亏损 ≤ 总资金 3%（按 20 万总资金计算 = 6000）
    "max_daily_loss": 6000,

    # 止损止盈
    "stop_loss_pct": 0.05,   # 每笔交易必须设置止损 -5%
    "take_profit_pct": 0.15,  # 止盈 15%

    # 连续亏损 3 次后暂停交易 1 天
    "max_consecutive_losses": 3,
    "consecutive_loss_pause_days": 1,
}
