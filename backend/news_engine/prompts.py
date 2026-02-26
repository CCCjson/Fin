"""
新闻分析 Prompt 模板
"""
from typing import Dict, List, Optional


def build_single_analysis_prompt(article: Dict, sentiment: Optional[Dict] = None) -> str:
    """
    构建单篇新闻深度分析 Prompt。

    article: {"title", "content", "source", "published_at", "symbol"}
    sentiment: {"sentiment", "confidence", "prob_positive", ...}
    """
    parts = [
        "# 新闻深度分析任务\n",
        "请对以下新闻进行专业的金融分析，输出结构化的 Markdown 报告。\n",
        "## 新闻信息\n",
        f"- **标题**: {article.get('title', '未知')}",
        f"- **来源**: {article.get('source', '未知')}",
        f"- **时间**: {article.get('published_at', '未知')}",
    ]

    if article.get("symbol"):
        parts.append(f"- **关联股票**: {article['symbol']}")

    parts.append(f"\n**正文内容**:\n{article.get('content', article.get('title', ''))}\n")

    if sentiment:
        parts.append("## BERT 情感分析结果\n")
        parts.append(f"- 情感倾向: **{sentiment.get('sentiment', '未知')}**")
        parts.append(f"- 置信度: {sentiment.get('confidence', 0):.1%}")
        parts.append(f"- 利好概率: {sentiment.get('prob_positive', 0):.1%}")
        parts.append(f"- 利空概率: {sentiment.get('prob_negative', 0):.1%}")
        parts.append(f"- 中性概率: {sentiment.get('prob_neutral', 0):.1%}\n")

    parts.append("""## 输出要求

请按以下结构输出分析报告：

### 1. 新闻摘要
用 2-3 句话概括新闻核心要点。

### 2. 市场影响分析
- 对相关股票/行业的短期影响（1-3 天）
- 对相关股票/行业的中期影响（1-3 个月）
- 影响的确定性和程度评估

### 3. 多空研判
- **利好因素**: 列出新闻中的积极信号
- **利空因素**: 列出新闻中的风险信号
- **综合判断**: 偏多/偏空/中性，给出理由

### 4. 风险提示
- 新闻可能存在的误导性
- 信息不完整可能导致的判断偏差
- 需要关注的后续事件

### 5. 操作建议
- 对持仓者的建议
- 对观望者的建议
- 关键价位或时间节点
""")

    return "\n".join(parts)


def build_report_prompt(articles: List[Dict], symbol: Optional[str] = None) -> str:
    """
    构建多篇新闻综合分析报告 Prompt。

    articles: [{"title", "content", "source", "published_at", "sentiment", "confidence"}, ...]
    symbol: 关联股票代码
    """
    # 限制 30 条，每条截断 100 字
    articles_trimmed = articles[:30]

    parts = [
        "# 新闻综合分析报告任务\n",
        f"请基于以下 {len(articles_trimmed)} 条新闻，生成一份专业的综合分析报告。\n",
    ]

    if symbol:
        parts.append(f"**分析对象**: {symbol}\n")

    parts.append("## 新闻列表\n")

    for i, art in enumerate(articles_trimmed, 1):
        sentiment_tag = ""
        if art.get("sentiment"):
            tag_map = {"positive": "利好", "negative": "利空", "neutral": "中性"}
            sentiment_tag = f" [{tag_map.get(art['sentiment'], art['sentiment'])} {art.get('confidence', 0):.0%}]"

        content_preview = (art.get("content") or art.get("title", ""))[:100]
        parts.append(
            f"### 新闻 {i}{sentiment_tag}\n"
            f"- **标题**: {art.get('title', '未知')}\n"
            f"- **来源**: {art.get('source', '未知')} | **时间**: {art.get('published_at', '未知')}\n"
            f"- **摘要**: {content_preview}\n"
        )

    parts.append("""## 输出要求

请生成以下结构的综合分析报告：

### 1. 情绪总览
- 利好/利空/中性新闻的数量分布和整体情绪倾向
- 市场情绪强度评估（强烈乐观/温和乐观/中性/温和悲观/强烈悲观）

### 2. 核心主题提取
- 提取 3-5 个核心主题，每个主题包含相关新闻编号和简要分析

### 3. 市场影响评估
- 短期影响（本周）
- 中期影响（1-3 个月）
- 影响确定性评级（高/中/低）

### 4. 投资机会与风险
- **机会**: 新闻中隐含的投资机会
- **风险**: 需要警惕的风险因素
- **关注点**: 后续需要跟踪的关键事件

### 5. 综合研判与建议
- 市场整体方向判断
- 建议的操作策略
- 仓位管理建议
""")

    return "\n".join(parts)
