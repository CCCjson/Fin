"""
股票深度分析器 — 庄股过滤 / 小盘画像 / 动态止盈止损 / 机构行为识别

纯计算模块，不访问 DB，所有数据由调用方传入。
"""
from typing import Dict, List, Optional
from loguru import logger


class StockAnalyzer:
    """对单只股票进行 4 个维度的深度分析"""

    # ------------------------------------------------------------------
    # P0: 庄股过滤 — 换手率 + 量价异常度 + 流动性等级
    # ------------------------------------------------------------------
    @staticmethod
    def analyze_manipulation_risk(
        quotes: List[Dict],
        fundamentals: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        庄股风险评估

        Args:
            quotes: 按日期升序排列的行情列表，每项至少包含
                     {close, volume, high, low} ，可选 {turnover}
            fundamentals: 基本面数据 {total_market_cap, float_market_cap, ...}

        Returns:
            {risk_score: 0-100, risk_level: low/medium/high,
             details: {turnover_analysis, volume_anomaly, liquidity_grade}}
        """
        if len(quotes) < 5:
            return None

        details: Dict = {}
        penalty = 0  # 累计风险罚分（越高越危险）

        # ---- 1) 换手率分析 ----
        turnovers = [q["turnover"] for q in quotes if q.get("turnover") is not None]
        if turnovers:
            avg_turnover = sum(turnovers) / len(turnovers)
            min_turnover = min(turnovers)
            if avg_turnover < 0.5:
                penalty += 40
                details["turnover_analysis"] = f"日均换手率极低 {avg_turnover:.2f}%，疑似庄股控盘"
            elif avg_turnover < 1.0:
                penalty += 20
                details["turnover_analysis"] = f"日均换手率偏低 {avg_turnover:.2f}%，流动性一般"
            else:
                details["turnover_analysis"] = f"日均换手率 {avg_turnover:.2f}%，流动性正常"

            # 连续多日极低换手率
            low_days = sum(1 for t in turnovers if t < 0.3)
            if low_days >= len(turnovers) * 0.6:
                penalty += 15
                details["turnover_analysis"] += f"，{low_days}天换手率<0.3%"
        else:
            details["turnover_analysis"] = "换手率数据缺失，跳过"

        # ---- 2) 量价异常度 ----
        volumes = [q["volume"] for q in quotes if q.get("volume") and q["volume"] > 0]
        if len(volumes) >= 5:
            avg_vol = sum(volumes) / len(volumes)
            # 单日成交量极端偏离（>5 倍均量或 <0.1 倍均量）
            extreme_days = sum(1 for v in volumes if v > avg_vol * 5 or v < avg_vol * 0.1)
            anomaly_ratio = extreme_days / len(volumes)

            if anomaly_ratio > 0.3:
                penalty += 25
                details["volume_anomaly"] = f"量价异常度高: {extreme_days}/{len(volumes)}天成交量极端偏离"
            elif anomaly_ratio > 0.1:
                penalty += 10
                details["volume_anomaly"] = f"量价异常度中等: {extreme_days}/{len(volumes)}天偏离"
            else:
                details["volume_anomaly"] = f"量价正常: {extreme_days}/{len(volumes)}天偏离"

            # 价格连续涨停但成交极小 → 高度控盘
            closes = [q["close"] for q in quotes if q.get("close")]
            if len(closes) >= 3:
                limit_up_low_vol = 0
                for i in range(1, len(closes)):
                    pct = (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if closes[i - 1] else 0
                    if pct > 9.5 and i < len(volumes) and volumes[i] < avg_vol * 0.5:
                        limit_up_low_vol += 1
                if limit_up_low_vol >= 2:
                    penalty += 20
                    details["volume_anomaly"] += f"，{limit_up_low_vol}次涨停缩量（高度控盘）"
        else:
            details["volume_anomaly"] = "成交量数据不足"

        # ---- 3) 流动性等级 ----
        if fundamentals and fundamentals.get("float_market_cap"):
            fmc = fundamentals["float_market_cap"]
            if fmc >= 100e8:  # >=100 亿
                grade = "A"
            elif fmc >= 30e8:
                grade = "B"
            elif fmc >= 10e8:
                grade = "C"
            else:
                grade = "D"
                penalty += 10
        elif fundamentals and fundamentals.get("total_market_cap"):
            tmc = fundamentals["total_market_cap"]
            if tmc >= 200e8:
                grade = "A"
            elif tmc >= 50e8:
                grade = "B"
            elif tmc >= 20e8:
                grade = "C"
            else:
                grade = "D"
                penalty += 10
        else:
            grade = "C"  # 数据缺失给中等

        details["liquidity_grade"] = grade

        # ---- 综合风险 ----
        risk_score = min(100, penalty)
        if risk_score >= 50:
            risk_level = "high"
        elif risk_score >= 25:
            risk_level = "medium"
        else:
            risk_level = "low"

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "details": details,
        }

    # ------------------------------------------------------------------
    # P1: 小盘画像 — 市值分类 + 缩量横盘 + 放量启动
    # ------------------------------------------------------------------
    @staticmethod
    def analyze_small_cap_profile(
        quotes: List[Dict],
        fundamentals: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        小盘股画像分析

        Returns:
            {cap_category, sideways_days, volume_breakout, description}
        """
        if len(quotes) < 5:
            return None

        # ---- 市值分类 ----
        cap_category = "unknown"
        cap_label = "未知"
        if fundamentals:
            tmc = fundamentals.get("total_market_cap")
            if tmc:
                if tmc >= 1000e8:
                    cap_category, cap_label = "mega", "超大盘(>1000亿)"
                elif tmc >= 300e8:
                    cap_category, cap_label = "large", "大盘(300-1000亿)"
                elif tmc >= 100e8:
                    cap_category, cap_label = "mid", "中盘(100-300亿)"
                elif tmc >= 30e8:
                    cap_category, cap_label = "small", "小盘(30-100亿)"
                else:
                    cap_category, cap_label = "micro", "微盘(<30亿)"

        # ---- 缩量横盘检测 ----
        closes = [q["close"] for q in quotes if q.get("close")]
        volumes = [q["volume"] for q in quotes if q.get("volume") and q["volume"] > 0]
        sideways_days = 0

        if len(closes) >= 10 and len(volumes) >= 10:
            avg_vol = sum(volumes) / len(volumes)
            recent_closes = closes[-20:] if len(closes) >= 20 else closes
            max_c, min_c = max(recent_closes), min(recent_closes)
            mid_c = (max_c + min_c) / 2

            # 横盘：最高最低价差 < 10%
            spread_pct = (max_c - min_c) / mid_c * 100 if mid_c else 0

            if spread_pct < 10:
                # 统计连续缩量天数（从最近往前数）
                recent_vols = volumes[-20:] if len(volumes) >= 20 else volumes
                for v in reversed(recent_vols):
                    if v < avg_vol * 0.8:
                        sideways_days += 1
                    else:
                        break

        # ---- 放量启动检测 ----
        volume_breakout = False
        breakout_detail = ""
        if len(volumes) >= 5 and len(closes) >= 3:
            avg_vol_10 = sum(volumes[-10:]) / len(volumes[-10:]) if len(volumes) >= 10 else sum(volumes) / len(volumes)
            latest_vol = volumes[-1]
            latest_close = closes[-1]
            prev_close = closes[-2] if len(closes) >= 2 else latest_close

            # 放量启动：最近 1 日成交量 > 2 倍近 10 日均量 + 收涨
            if avg_vol_10 > 0 and latest_vol > avg_vol_10 * 2 and latest_close > prev_close:
                volume_breakout = True
                ratio = latest_vol / avg_vol_10
                pct_change = (latest_close - prev_close) / prev_close * 100
                breakout_detail = f"放量{ratio:.1f}倍启动，涨{pct_change:.1f}%"

        return {
            "cap_category": cap_category,
            "cap_label": cap_label,
            "sideways_days": sideways_days,
            "volume_breakout": volume_breakout,
            "breakout_detail": breakout_detail,
        }

    # ------------------------------------------------------------------
    # P2: 动态止盈止损 — ATR 止损 / 追踪止损 / 分批止盈 / 盈亏比
    # ------------------------------------------------------------------
    @staticmethod
    def calculate_dynamic_levels(
        entry_price: Optional[float],
        quotes: List[Dict],
        atr: Optional[float] = None,
        signal_type: str = "BUY",
    ) -> Optional[Dict]:
        """
        基于 ATR 计算动态止盈止损位

        Args:
            entry_price: 入场价（买入推荐用信号触发价，持仓用均价）
            quotes: 行情列表（升序）
            atr: 14 日 ATR（若 None 则用 close * 2% 估算）
            signal_type: BUY / SELL

        Returns:
            {atr_value, atr_stop_loss, trailing_stop, take_profit_levels, risk_reward_ratio}
        """
        if not entry_price or entry_price <= 0 or len(quotes) < 3:
            return None

        # ATR 估算
        if atr is None or atr <= 0:
            latest_close = quotes[-1].get("close", entry_price)
            atr = latest_close * 0.02  # 默认用 2% 估算

        latest_close = quotes[-1].get("close", entry_price)

        if signal_type == "BUY":
            atr_stop_loss = round(entry_price - 2 * atr, 2)
            trailing_stop = round(latest_close - 1.5 * atr, 2)

            tp1 = round(entry_price + 1 * atr, 2)
            tp2 = round(entry_price + 2 * atr, 2)
            tp3 = round(entry_price + 3 * atr, 2)

            # 盈亏比 = 到第二目标位的收益 / 止损距离
            risk = entry_price - atr_stop_loss
            reward = tp2 - entry_price
            rr_ratio = round(reward / risk, 2) if risk > 0 else 0
        else:
            # SELL 信号的反向逻辑
            atr_stop_loss = round(entry_price + 2 * atr, 2)
            trailing_stop = round(latest_close + 1.5 * atr, 2)

            tp1 = round(entry_price - 1 * atr, 2)
            tp2 = round(entry_price - 2 * atr, 2)
            tp3 = round(entry_price - 3 * atr, 2)

            risk = atr_stop_loss - entry_price
            reward = entry_price - tp2
            rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        return {
            "atr_value": round(atr, 2),
            "atr_stop_loss": atr_stop_loss,
            "trailing_stop": trailing_stop,
            "take_profit_levels": [
                {"level": 1, "price": tp1, "action": "减仓1/3"},
                {"level": 2, "price": tp2, "action": "减仓1/3"},
                {"level": 3, "price": tp3, "action": "清仓"},
            ],
            "risk_reward_ratio": rr_ratio,
        }

    # ------------------------------------------------------------------
    # P3: 机构行为 — 止损猎杀 / 假突破 / 量价配合度
    # ------------------------------------------------------------------
    @staticmethod
    def analyze_market_behavior(quotes: List[Dict]) -> Optional[Dict]:
        """
        机构行为识别

        Returns:
            {stop_hunt_count, fake_breakout_count, volume_price_alignment,
             details: {stop_hunts, fake_breakouts}}
        """
        if len(quotes) < 5:
            return None

        stop_hunts = []
        fake_breakouts = []

        closes = [q.get("close", 0) for q in quotes]
        highs = [q.get("high", 0) for q in quotes]
        lows = [q.get("low", 0) for q in quotes]
        volumes = [q.get("volume", 0) or 0 for q in quotes]

        # ---- 1) 止损猎杀检测（长下影线 + 收盘回升） ----
        for i in range(len(quotes)):
            q = quotes[i]
            o, h, l, c = q.get("open", 0), q.get("high", 0), q.get("low", 0), q.get("close", 0)
            if not all([o, h, l, c]) or h == l:
                continue

            body = abs(c - o)
            lower_shadow = min(o, c) - l
            total_range = h - l

            # 长下影线：下影线 > 实体2倍 且 > 整体50%
            if lower_shadow > body * 2 and lower_shadow > total_range * 0.5:
                # 收盘价在上半部分
                if c > (h + l) / 2:
                    stop_hunts.append({
                        "date": q.get("date", ""),
                        "detail": f"长下影线 下影{lower_shadow:.2f} 实体{body:.2f}",
                    })

        # ---- 2) 假突破检测（突破前高次日跌回） ----
        if len(closes) >= 10:
            for i in range(5, len(closes) - 1):
                # 计算前 5 日最高
                prev_high = max(highs[i - 5:i])
                # 当日突破前高
                if highs[i] > prev_high and closes[i] > prev_high:
                    # 次日跌回前高下方
                    if closes[i + 1] < prev_high:
                        fake_breakouts.append({
                            "date": quotes[i].get("date", ""),
                            "detail": f"突破{prev_high:.2f}后次日跌回{closes[i+1]:.2f}",
                        })

        # ---- 3) 量价配合度 ----
        alignment_count = 0
        total_check = 0
        for i in range(1, len(closes)):
            if closes[i - 1] == 0 or volumes[i] == 0 or volumes[i - 1] == 0:
                continue
            total_check += 1
            price_up = closes[i] > closes[i - 1]
            vol_up = volumes[i] > volumes[i - 1]
            # 价涨量增 或 价跌量缩 → 配合
            if (price_up and vol_up) or (not price_up and not vol_up):
                alignment_count += 1

        alignment_pct = round(alignment_count / total_check * 100, 1) if total_check > 0 else 0

        if alignment_pct >= 70:
            alignment_desc = "量价高度配合"
        elif alignment_pct >= 50:
            alignment_desc = "量价配合度一般"
        else:
            alignment_desc = "量价背离明显"

        return {
            "stop_hunt_count": len(stop_hunts),
            "fake_breakout_count": len(fake_breakouts),
            "volume_price_alignment": alignment_pct,
            "volume_price_desc": alignment_desc,
            "details": {
                "stop_hunts": stop_hunts[-3:],  # 只保留最近 3 次
                "fake_breakouts": fake_breakouts[-3:],
            },
        }
