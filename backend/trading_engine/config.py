"""
交易引擎配置

凭证一律从环境变量读取（.env），不得硬编码进仓库。
"""
import os

# OpenCTP 7x24 测试环境配置（账号/密码从 .env 读取：OPENCTP_USER_ID / OPENCTP_PASSWORD）
OPENCTP_CONFIG = {
    # 经纪商配置
    "broker_id": os.getenv("OPENCTP_BROKER_ID", "9999"),  # SimNow 经纪商代码
    "user_id": os.getenv("OPENCTP_USER_ID", ""),
    "password": os.getenv("OPENCTP_PASSWORD", ""),

    # 认证信息
    "app_id": os.getenv("OPENCTP_APP_ID", "simnow_client_test"),
    "auth_code": os.getenv("OPENCTP_AUTH_CODE", "0000000000000000"),

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

    # 单日最大亏损 ≤ 总资金 3%（动态计算）
    "max_daily_loss_pct": 0.03,

    # 止损止盈
    "stop_loss_pct": 0.05,   # 每笔交易必须设置止损 -5%
    "take_profit_pct": 0.15,  # 止盈 15%

    # 连续亏损 3 次后暂停交易 1 天
    "max_consecutive_losses": 3,
    "consecutive_loss_pause_days": 1,
}
