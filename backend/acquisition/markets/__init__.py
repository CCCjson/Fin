"""
行情 / 财务数据获取 —— 各市场 fetcher 与主备源路由。

`eastmoney_crawler` 此前住在 `scripts/`，害得三个生产模块（daily_updater /
deep_history.a_share_job / report_engine.web_searcher）各写一段 `sys.path.insert`
才 import 得到它。搬进正位后那三段补丁一并消失。
"""
