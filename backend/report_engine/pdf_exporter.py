"""
报告 PDF 导出模块

Markdown → HTML（markdown 库） → PDF（weasyprint）
"""
import os
import platform

# macOS + Homebrew: weasyprint 依赖 gobject/pango 等 C 库
# conda 环境下 DYLD_FALLBACK_LIBRARY_PATH 可能未包含 /opt/homebrew/lib
if platform.system() == "Darwin":
    _brew_lib = "/opt/homebrew/lib"
    _fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if _brew_lib not in _fallback:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{_brew_lib}:{_fallback}" if _fallback else _brew_lib

import markdown
from weasyprint import HTML
from loguru import logger


# ── CSS 样式 ──
_CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm;
    @bottom-center {
        content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
        font-size: 9px;
        color: #999;
    }
}

body {
    font-family: "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 11pt;
    line-height: 1.8;
    color: #1a1a1a;
}

/* 标题 */
h1 {
    font-size: 22pt;
    font-weight: 700;
    text-align: center;
    margin-top: 0;
    margin-bottom: 0.8em;
    padding-bottom: 0.5em;
    border-bottom: 2px solid #6366f1;
    color: #1e1b4b;
}

h2 {
    font-size: 15pt;
    font-weight: 700;
    color: #312e81;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    padding-bottom: 0.3em;
    border-bottom: 1px solid #e5e7eb;
    page-break-after: avoid;
}

h3 {
    font-size: 12pt;
    font-weight: 600;
    color: #4338ca;
    margin-top: 1.2em;
    margin-bottom: 0.4em;
    page-break-after: avoid;
}

h4 {
    font-size: 11pt;
    font-weight: 600;
    color: #333;
    margin-top: 1em;
    margin-bottom: 0.3em;
}

/* 段落与列表 */
p {
    margin-bottom: 0.6em;
    text-align: justify;
}

ul, ol {
    margin: 0.4em 0 0.8em 1.5em;
    padding: 0;
}

li {
    margin-bottom: 0.3em;
}

/* 表格 */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 0.8em 0;
    font-size: 9.5pt;
    page-break-inside: auto;
}

thead {
    background-color: #f1f5f9;
}

th {
    padding: 6px 8px;
    text-align: left;
    font-weight: 600;
    color: #475569;
    border-bottom: 2px solid #cbd5e1;
    font-size: 9pt;
}

td {
    padding: 5px 8px;
    border-bottom: 1px solid #e2e8f0;
    color: #334155;
}

tr:nth-child(even) {
    background-color: #f8fafc;
}

/* 代码块 */
code {
    font-family: "SF Mono", "Fira Code", "Consolas", monospace;
    font-size: 9pt;
    background-color: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
}

pre {
    background-color: #f1f5f9;
    padding: 10px 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 9pt;
    line-height: 1.5;
    margin: 0.8em 0;
}

pre code {
    background: none;
    padding: 0;
}

/* 引用块 */
blockquote {
    border-left: 3px solid #6366f1;
    margin: 0.8em 0;
    padding: 0.4em 1em;
    background-color: #f5f3ff;
    color: #4338ca;
}

blockquote p {
    margin: 0.2em 0;
}

/* 强调 */
strong {
    color: #111;
    font-weight: 700;
}

em {
    color: #6366f1;
    font-style: normal;
    font-weight: 500;
}

/* 水平线 */
hr {
    border: none;
    border-top: 1px solid #e5e7eb;
    margin: 1.5em 0;
}

/* 自动在 h2 前分页（首个 h2 除外） */
h2 {
    page-break-before: auto;
}
"""


class ReportPDFExporter:
    """将 Markdown 报告导出为 PDF"""

    def __init__(self):
        self._md = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "nl2br"],
            output_format="html",
        )

    def export(self, markdown_content: str, title: str = "") -> bytes:
        """
        将 Markdown 转换为 PDF 字节流

        Args:
            markdown_content: Markdown 格式的报告内容
            title: 报告标题（用于 HTML <title>）

        Returns:
            PDF 文件的 bytes
        """
        self._md.reset()
        html_body = self._md.convert(markdown_content)

        html_full = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>{_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

        logger.info(f"正在生成 PDF: {title}")
        pdf_bytes = HTML(string=html_full).write_pdf()
        logger.info(f"PDF 生成完成, 大小: {len(pdf_bytes)} bytes")
        return pdf_bytes
