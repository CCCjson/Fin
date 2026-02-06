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

# 风险控制配置
RISK_CONFIG = {
    # 单笔订单限制
    "max_order_value": 50000,  # 单笔最大金额
    "max_order_quantity": 1000,  # 单笔最大数量

    # 持仓限制
    "max_position_value": 200000,  # 最大持仓市值
    "max_position_pct": 0.3,  # 单个品种最大持仓比例

    # 损失限制
    "max_daily_loss": 5000,  # 最大单日亏损

    # 最低现金保留
    "min_cash": 10000,  # 最低现金保留

    # 止损止盈
    "stop_loss_pct": 0.05,  # 止损 5%
    "take_profit_pct": 0.10,  # 止盈 10%

    # 交易时间限制（可选，Paper Trading 可不启用）
    # "allow_trading_hours": [
    #     ("09:00", "11:30"),  # 上午
    #     ("13:00", "15:00"),  # 下午
    # ],
}
