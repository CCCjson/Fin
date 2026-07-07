"""
AI 投资顾问 — Prompt 构建器
构建 system prompt + 首次分析 / 追问的 user prompt。
"""
from typing import Dict, Optional


SYSTEM_PROMPT = """你是我的私人量化交易顾问，直接告诉我该怎么操作，不要废话。
你的分析基于量化系统提供的真实数据。

核心要求：
1. 直接给出明确的买/卖/持有/观望操作指令，不要模棱两可
2. 所有判断必须引用具体指标数值，禁止说"技术面良好"等空话
3. 多指标交叉验证，给出确定性最高的结论
4. 必须给出具体的入场价、止损价、止盈价，精确到小数点
5. 禁止说"不构成投资建议""建议咨询专业人士"之类的废话，我就是要你的投资建议
6. 使用 Markdown 格式输出，结构清晰
7. K线形态名称已翻译为中文，直接使用中文名称即可，禁止输出英文形态名

篇幅纪律（必须遵守）：
- 全文不超过 1200 字。我要的是决策依据，不是研究报告
- 同一结论只说一次：操作指令、止损止盈价位只在「操作计划」出现，禁止在开头/结尾/风险段重复
- 禁止展示算术过程（如"18.59 - 17.81 = 0.78 元，涨幅 4.38%"），直接给结果
- 数据里已有的原始表格（近日行情、指标快照、信号列表）不要复述或重新制表，只引用与结论直接相关的数值
- 每个指标一句话说清"数值 + 含义"，不要一个指标一个小节

分析框架（首次分析时遵循，共 4 段）：
1. **结论** — 第一句话：买/卖/持有/观望 + 信心指数（0-100）+ 一句话核心理由
2. **关键证据** — 用一个合并表格列出支撑结论的核心指标（均线/MACD/RSI/KDJ/布林/量比/信号强度，只列用到的），表格后用 2-3 句话点出多空分歧点。若数据中提供了「标的质量分析」「机构行为分析」，引用庄股风险、止损猎杀/假突破次数；庄股风险为中/高必须明确警示
3. **操作计划** — 一个表格写全：入场价、ATR 止损位（atr_stop_loss）、追踪止损位（trailing_stop）、T1/T2/T3 分批止盈（take_profit_levels）、盈亏比（risk_reward_ratio）、建议仓位
4. **失效条件** — 最多 3 条：什么价位/情形下判断失效，各一句话对策

回答追问时，基于对话上下文中的数据，直接回答问题，给出明确操作建议，同样遵守篇幅纪律。
"""


class AdvisorPromptBuilder:
    """构建 system prompt 和 user prompt"""

    @staticmethod
    def build_system_prompt() -> str:
        return SYSTEM_PROMPT.strip()

    @staticmethod
    def build_first_analysis_prompt(ctx: Dict) -> str:
        """构建首次分析的 user prompt"""
        symbol = ctx.get("symbol", "")
        name = ctx.get("name", symbol)
        parts = [f"请对 **{symbol}**（{name}）进行全面技术分析并给出投资建议。\n"]
        parts.append("以下是该股票的量化分析数据：\n")

        # 行情概览
        price = ctx.get("price_data")
        if price and price.get("latest"):
            lt = price["latest"]
            parts.append("### 行情概览\n")
            parts.append(f"- 最新收盘价: ¥{lt['close']}")
            def _pct_str(key: str) -> str:
                v = price.get(key)
                return f"{v}%" if v is not None else "N/A"

            parts.append(f"- 5日涨跌: {_pct_str('change_5d_pct')}")
            parts.append(f"- 20日涨跌: {_pct_str('change_20d_pct')}")
            parts.append(f"- 60日涨跌: {_pct_str('change_60d_pct')}")
            parts.append(f"- 20日高点: ¥{price.get('high_20d', 'N/A')}  20日低点: ¥{price.get('low_20d', 'N/A')}")
            parts.append("")

            # 近10日行情表
            recent = price.get("recent_10d", [])
            if recent:
                parts.append("### 近10日行情\n")
                parts.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |")
                parts.append("|------|------|------|------|------|--------|")
                for r in recent:
                    parts.append(
                        f"| {r['date']} | {r['open']} | {r['high']} | {r['low']} | {r['close']} | {r['volume']:,} |"
                    )
                parts.append("")

        # 技术指标
        ind = ctx.get("indicators")
        if ind:
            parts.append("### 技术指标快照\n")
            parts.append("| 指标 | 当前值 | 状态 |")
            parts.append("|------|--------|------|")

            # 均线系统
            ma_vals = [ind.get(f"ma{p}") for p in [5, 10, 20, 60]]
            ma_str = " / ".join(str(v) if v else "N/A" for v in ma_vals)
            ma_status = _ma_status(ma_vals)
            parts.append(f"| MA(5/10/20/60) | {ma_str} | {ma_status} |")

            # MACD
            dif, dea, hist = ind.get("macd_dif"), ind.get("macd_dea"), ind.get("macd_hist")
            macd_st = "金叉" if (dif is not None and dea is not None and dif > dea) else "死叉"
            parts.append(f"| MACD | DIF={dif}, DEA={dea}, 柱={hist} | {macd_st} |")

            # RSI
            rsi = ind.get("rsi")
            rsi_st = "超买" if rsi and rsi > 70 else ("超卖" if rsi and rsi < 30 else "中性")
            parts.append(f"| RSI(14) | {rsi} | {rsi_st} |")

            # KDJ
            k, d, j = ind.get("kdj_k"), ind.get("kdj_d"), ind.get("kdj_j")
            kdj_st = "金叉" if (k is not None and d is not None and k > d) else "死叉"
            parts.append(f"| KDJ | K={k}, D={d}, J={j} | {kdj_st} |")

            # BOLL
            bu, bm, bl = ind.get("boll_upper"), ind.get("boll_middle"), ind.get("boll_lower")
            if price and price.get("latest"):
                close = price["latest"]["close"]
                if bu and bl:
                    pos = "上轨附近" if close > bu * 0.98 else ("下轨附近" if close < bl * 1.02 else "中轨附近")
                else:
                    pos = "-"
            else:
                pos = "-"
            parts.append(f"| BOLL | 上={bu}, 中={bm}, 下={bl} | {pos} |")

            # ATR
            atr = ind.get("atr")
            parts.append(f"| ATR(14) | {atr} | - |")

            # 量比
            vr = ind.get("volume_ratio")
            vr_st = "放量" if vr and vr > 1.5 else ("缩量" if vr and vr < 0.7 else "正常")
            parts.append(f"| 量比 | {vr} | {vr_st} |")
            parts.append("")

        # ── 标的质量分析 ──
        mr = ctx.get("manipulation_risk")
        if mr:
            emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(mr.get("risk_level", ""), "⚪")
            label = {"low": "低风险", "medium": "中等风险", "high": "高风险"}.get(mr.get("risk_level", ""), "未知")
            parts.append("### 标的质量分析\n")
            parts.append(f"- 庄股风险: {emoji} {label} (风险分 {mr.get('risk_score', 'N/A')}/100)")
            details = mr.get("details", {})
            if details.get("turnover_analysis"):
                parts.append(f"- 换手率: {details['turnover_analysis']}")
            if details.get("volume_anomaly"):
                parts.append(f"- 量价异常: {details['volume_anomaly']}")
            if details.get("liquidity_grade"):
                parts.append(f"- 流动性等级: {details['liquidity_grade']}")
            parts.append("")

        # ── 市值画像 ──
        scp = ctx.get("small_cap_profile")
        if scp:
            parts.append("### 市值画像\n")
            parts.append(f"- 市值分类: {scp.get('cap_label', '未知')}")
            if scp.get("sideways_days", 0) > 0:
                parts.append(f"- 缩量横盘: {scp['sideways_days']}天")
            if scp.get("volume_breakout"):
                parts.append(f"- 放量启动: {scp.get('breakout_detail', '是')}")
            parts.append("")

        # ── ATR 动态止盈止损 ──
        dl = ctx.get("dynamic_levels")
        if dl:
            parts.append("### ATR 动态止盈止损\n")
            parts.append(f"- ATR(14): {dl.get('atr_value', 'N/A')}")
            parts.append(f"- ATR 止损位: {dl.get('atr_stop_loss', 'N/A')}")
            parts.append(f"- 追踪止损位: {dl.get('trailing_stop', 'N/A')}")
            tp_levels = dl.get("take_profit_levels", [])
            for t in tp_levels:
                parts.append(f"- 止盈 T{t['level']}: {t['price']} ({t['action']})")
            parts.append(f"- 盈亏比: {dl.get('risk_reward_ratio', 'N/A')}")
            parts.append("")

        # ── 机构行为分析 ──
        mb = ctx.get("market_behavior")
        if mb:
            parts.append("### 机构行为分析\n")
            parts.append(f"- 止损猎杀次数: {mb.get('stop_hunt_count', 0)}")
            parts.append(f"- 假突破次数: {mb.get('fake_breakout_count', 0)}")
            parts.append(f"- 量价配合度: {mb.get('volume_price_alignment', 'N/A')}% ({mb.get('volume_price_desc', '')})")
            details = mb.get("details", {})
            for sh in details.get("stop_hunts", []):
                parts.append(f"- 止损猎杀: {sh.get('date', '')} — {sh.get('detail', '')}")
            for fb in details.get("fake_breakouts", []):
                parts.append(f"- 假突破: {fb.get('date', '')} — {fb.get('detail', '')}")
            parts.append("")

        # 策略信号
        signals = ctx.get("signals")
        if signals:
            parts.append("### 近期交易信号\n")
            parts.append("| 日期 | 类型 | 强度 | 策略 | 理由 |")
            parts.append("|------|------|------|------|------|")
            for s in signals[-20:]:
                date_str = str(s.get("date", ""))[:10]
                sig_type = s.get("signal_type", "")
                strength = s.get("strength", "")
                strategy = s.get("strategy", "")
                reasons = "; ".join(s.get("reasons", [])) if isinstance(s.get("reasons"), list) else str(s.get("reasons", ""))
                parts.append(f"| {date_str} | {sig_type} | {strength} | {strategy} | {reasons} |")
            parts.append("")

        # K线形态
        patterns = ctx.get("patterns")
        if patterns:
            parts.append("### K线形态\n")
            for p in patterns:
                parts.append(f"- {p.get('date', '')}: {p.get('pattern', '')}")
            parts.append("")

        # 信号统计
        stats = ctx.get("signal_stats")
        if stats:
            parts.append("### 信号统计（近90天）\n")
            parts.append(f"- 总信号数: {stats.get('total_signals', 0)}")
            parts.append(f"- 买入信号: {stats.get('buy_signals', 0)}")
            parts.append(f"- 卖出信号: {stats.get('sell_signals', 0)}")
            parts.append(f"- 平均强度: {stats.get('avg_strength', 0)}")
            parts.append("")

        # 基本面数据
        fund = ctx.get("fundamentals")
        if fund:
            parts.append("### 基本面估值\n")
            def _fv(key: str, label: str, suffix: str = "") -> str:
                v = fund.get(key)
                if v is None:
                    return f"- {label}: N/A"
                if key in ("total_market_cap", "float_market_cap") and v > 1e8:
                    return f"- {label}: {v / 1e8:.2f}亿元"
                return f"- {label}: {v}{suffix}"
            parts.append(_fv("pe", "市盈率(动态)"))
            parts.append(_fv("pe_ttm", "市盈率(TTM)"))
            parts.append(_fv("pb", "市净率"))
            parts.append(_fv("total_market_cap", "总市值"))
            parts.append(_fv("float_market_cap", "流通市值"))
            parts.append(_fv("roe", "ROE", "%"))
            parts.append("")

        # 个股相关新闻
        news = ctx.get("web_news")
        if news:
            parts.append("### 该股票最新新闻\n")
            for n in news[:10]:
                title = n.get("title", "")
                src = n.get("source", "")
                t = n.get("time", "")[:16]
                body = n.get("body", "")
                line = f"- [{t}] {title}"
                if src:
                    line += f" ({src})"
                if body:
                    line += f"\n  > {body}"
                parts.append(line)
            parts.append("")

        return "\n".join(parts)


def _ma_status(ma_vals: list) -> str:
    """判断均线排列"""
    valid = [v for v in ma_vals if v is not None]
    if len(valid) < 2:
        return "-"
    if all(valid[i] >= valid[i + 1] for i in range(len(valid) - 1)):
        return "多头排列"
    if all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1)):
        return "空头排列"
    return "交叉排列"
