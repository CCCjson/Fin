"""把 13.2 之前那份**整篇**投研报告按章节切开，便于与新章节样本对照。

13.2 把全量报告拆成五个章节工具后，`capture_samples.py` 每章各出一个 .md。
旧样本是一整篇（`before_step13/report.md`），先用本脚本切开：

    conda run -n quant python tests/baseline/split_report_chapters.py \
        scripts/data/baseline_samples/before_step13/report.md

    diff -u <before>/chapters/ch2.md <after>/report_market.md

**能力边界（实测，别高估它）**：旧样本是对话流正文，章节边界并没有被可靠地标注——
模型写 Ch2 时用了 `## 2. 市场总览`，写 Ch3/Ch4/Ch7 时只留下 `### 3.1` 这类带编号的
三级标题，而 Ch5「持仓诊断」/ Ch6「上期回顾」/ Ch8「买入推荐」压根没有编号标题
（`### 汇总统计`、`### 603213.SH 镇洋发展`）。所以本脚本只能定位到其中一部分，
定位不到的会显式列出来，需要人工对照。

真正保证「搬运没改坏内容」的是 `tests/report/test_section_prompt_parity.py`：
它锁死新章节工具喂给 LLM 的 prompt 与退役前逐字相同。输出的差异只可能来自 LLM
随机性。本脚本只是让人工抽查省点事。
"""
from __future__ import annotations

import argparse
import pathlib
import re

# 章节号 → 现在归哪个章节工具（供人工对照时不必回头翻文档）
CHAPTER_OWNER = {
    1: "（已退役：纵览由 MoneyBill 主 agent 撰写）",
    2: "report_market（前半段）",
    3: "report_news",
    4: "report_market（后半段）",
    5: "report_positions",
    6: "report_strategy（前半段）",
    7: "report_strategy（后半段）",
    8: "report_picks",
}

# 模型不总是老老实实写 `## 3. 新闻…`，常常直接从 `### 3.1 宏观政策` 开始。
# 两种都得认；而 `### 一、`（中文序号）与 `#### 1）`（四级）都不是章节界标。
_CH_HEADING = re.compile(r"^##\s+(\d+)[.、]", re.MULTILINE)
_SUB_HEADING = re.compile(r"^###\s+(\d+)\.\d+", re.MULTILINE)


def split(text: str) -> dict[int, str]:
    """按章节号切段。返回 {章节号: 该章全文（含标题行）}。

    注意样本是**对话流正文**，章节按生成顺序排列（2,3,4,6,5,7,8,1），不是按章节号。
    """
    marks = sorted(
        [(m.start(), int(m.group(1))) for m in _CH_HEADING.finditer(text)]
        + [(m.start(), int(m.group(1))) for m in _SUB_HEADING.finditer(text)]
    )
    # 只在章节号真的变了的地方切；同章内的 `### N.M` 子节不切
    bounds = [mk for i, mk in enumerate(marks) if i == 0 or mk[1] != marks[i - 1][1]]
    if not bounds:
        return {}

    chapters: dict[int, str] = {}
    for i, (pos, num) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        body = text[pos:end].strip()
        # 同一章可能被分批写成多段（Ch8 三批），拼起来
        chapters[num] = (chapters[num] + "\n\n" + body) if num in chapters else body
    return chapters


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_md", type=pathlib.Path, help="整篇报告的 markdown")
    args = parser.parse_args()

    chapters = split(args.report_md.read_text())
    if not chapters:
        raise SystemExit(f"{args.report_md} 里没找到任何带编号的章节标题")

    out_dir = args.report_md.parent / "chapters"
    out_dir.mkdir(exist_ok=True)
    for num in sorted(chapters):
        path = out_dir / f"ch{num}.md"
        path.write_text(chapters[num] + "\n")
        print(f"ch{num}: {len(chapters[num]):>6} 字  → {CHAPTER_OWNER.get(num, '?')}")

    missing = sorted(set(CHAPTER_OWNER) - set(chapters))
    if missing:
        print("\n⚠️ 以下章节没有带编号的标题，无法自动定位，需人工对照：")
        for num in missing:
            print(f"   ch{num} → {CHAPTER_OWNER[num]}")
        print("   （切出来的相邻章节文件里会混进它们的正文）")
    print(f"\n已切分到 {out_dir}")


if __name__ == "__main__":
    main()
