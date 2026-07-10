"""章节正文的纯文本工具 —— 无 I/O、无 LLM、无 DB。

13.2 拆解时从 `generator.py` 搬出来，让新的章节成稿器（`section_writer.py`）与
旧的全量报告路径（`generator.py`，随后退役）共用同一份真源，而不是各抄一遍。
"""
import re

from common.market import to_bare_code

# GPT 在 Ch8 续批里常把二级标题再写一遍（"## 8. 重点买入标的深度分析"
# 或 "## 8、..." 等变体），累积正文时要剥掉，否则一章出现多个同级标题。
_DUP_CH8_HEADING = re.compile(r'^\s*##\s+8[.、\s：:][^\n]*\n?', flags=re.MULTILINE)

_OP_HEADER_KWS = ("明日操作", "操作建议", "操作：", "建议：")
_OP_CONTENT_KWS = ("✅", "📈", "📉", "🚨", "⚠️", "继续持有", "加仓", "减仓",
                   "止损", "清仓", "观察", "买入", "卖出", "持有")


def strip_duplicate_ch8_heading(text: str) -> str:
    """剥除 Ch8 续批里重复输出的 `## 8. xxx` 二级标题。"""
    return _DUP_CH8_HEADING.sub('', text, count=0).lstrip()


def validate_ch8_output(output_text: str, batch_stocks: list[dict]) -> list[dict]:
    """检查 Ch8 输出是否覆盖了全部标的，返回缺失的股票列表。"""
    missing = []
    for stock in batch_stocks:
        symbol = stock.get("symbol", "")
        if not symbol:
            continue
        # 同时检查完整代码(000001.SZ)、纯数字部分(000001)和股票名称
        # GPT 可能只写名称而省略代码，避免误判导致不必要的重试
        code_only = to_bare_code(symbol)
        name = stock.get("name", "")
        if (symbol not in output_text
                and code_only not in output_text
                and (not name or name not in output_text)):
            missing.append(stock)
    return missing


def extract_conclusion(text: str, max_chars: int = 500) -> str:
    """从章节全文中提取结论段（## 标题后到第一个 ### 之前的内容）。"""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    lines = text.split("\n")
    conclusion_lines = []
    started = False
    for line in lines:
        if line.startswith("## "):
            started = True
            conclusion_lines.append(line)
            continue
        if started and line.startswith("### "):
            break
        conclusion_lines.append(line)

    result = "\n".join(conclusion_lines).strip()
    if len(result) > max_chars:
        result = result[:max_chars] + "..."
    return result if result else text[:max_chars] + "..."


def extract_per_stock_ops(text: str, chapter_label: str, max_stocks: int = 15) -> str:
    """
    从含个股 ### 子节的章节（如 Ch5 持仓诊断、Ch8 买入推荐）中，
    提取每只股票的「明日操作」结论，格式：
        【Ch5 持仓诊断 — 每只股票操作结论】
        - 金花股份(600080.SH): 📈 明日可加仓（8.80-8.95元）
        - 同益中(688722.SH): ✅ 继续持有，观察
    供后续批次/章节对照，确保操作建议不矛盾。
    """
    lines = text.split("\n")
    results: list[str] = []
    current_stock = None

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # 识别个股标题 ###
        if stripped.startswith("### "):
            current_stock = stripped[4:].strip()
            i += 1
            continue

        if current_stock:
            # 情况1：「明日操作：📈 内容」在同一行（内容非空）
            for kw in _OP_HEADER_KWS:
                if kw in stripped:
                    after = stripped[stripped.index(kw) + len(kw):].lstrip("：: ").strip()
                    if after and len(after) > 3:
                        clean = after.lstrip("-*>🔹•").strip()
                        results.append(f"  - {current_stock}: {clean[:120]}")
                        current_stock = None
                        break
                    else:
                        # 情况2：操作内容在下一行
                        j = i + 1
                        while j < len(lines) and j <= i + 3:
                            next_stripped = lines[j].strip()
                            if next_stripped and any(c in next_stripped for c in _OP_CONTENT_KWS):
                                clean = next_stripped.lstrip("-*>🔹•📈📉✅🚨⚠️").strip()
                                results.append(f"  - {current_stock}: {clean[:120]}")
                                current_stock = None
                                break
                            elif next_stripped:
                                # 非空但没有操作关键词，也直接用
                                clean = next_stripped.lstrip("-*>🔹•").strip()
                                if len(clean) > 5:
                                    results.append(f"  - {current_stock}: {clean[:120]}")
                                    current_stock = None
                                break
                            j += 1
                    break
            else:
                # 没有操作标记词，但行本身直接含操作内容关键词
                if any(kw in stripped for kw in _OP_CONTENT_KWS) and len(stripped) > 5:
                    clean = stripped.lstrip("-*>🔹•📈📉✅🚨⚠️").strip()
                    results.append(f"  - {current_stock}: {clean[:120]}")
                    current_stock = None

        if current_stock is None and len(results) >= max_stocks:
            break
        i += 1

    if not results:
        return ""

    header = f"【{chapter_label} — 各股操作结论（后续章节必须与此保持一致，不得矛盾）】"
    return header + "\n" + "\n".join(results)
