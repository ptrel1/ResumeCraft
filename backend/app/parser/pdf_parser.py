"""PDF 提取：将 PDF 简历转成 HTML / md 文本。

使用 PyMuPDF 提取文本块（坐标/字体/字号/颜色），输出：
1. to_html()  —— 定位式 HTML（最大程度还原版式，供预览）
2. to_text()   —— 纯文本（按阅读顺序，供后续转 md）
"""
from __future__ import annotations

import html
from typing import List

import fitz  # PyMuPDF


def open_pdf(path: str):
    return fitz.open(path)


def to_text(path: str, ordered: bool = True) -> str:
    """按阅读顺序提取纯文本。"""
    doc = open_pdf(path)
    parts: List[str] = []
    for page in doc:
        if ordered:
            parts.append(_extract_ordered_text(page))
        else:
            parts.append(page.get_text())
    return "\n".join(parts)


def _extract_ordered_text(page) -> str:
    """按 y 从上到下、同一行按 x 排序，还原阅读顺序。"""
    d = page.get_text("dict")
    spans = []
    for b in d["blocks"]:
        if b["type"] == 1:
            continue
        for l in b["lines"]:
            for s in l["spans"]:
                if s["text"].strip():
                    spans.append((round(s["bbox"][1], 1), round(s["bbox"][0], 1), s["text"]))
    spans.sort(key=lambda t: (t[0], t[1]))
    return "\n".join(t[2] for t in spans)


def to_html(path: str, background: bool = True) -> str:
    """生成定位式 HTML，还原 PDF 版式（坐标 + 字体 + 字号 + 颜色）。"""
    doc = open_pdf(path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height

    # 提取背景色（第一个填充对象）
    bg_css = ""
    if background:
        for dr in page.get_drawings():
            if dr.get("fill"):
                f = dr["fill"]
                hexc = "#%02x%02x%02x" % (int(f[0] * 255), int(f[1] * 255), int(f[2] * 255))
                bg_css = f"background:{hexc};"
                break

    d = page.get_text("dict")
    spans_html = []
    for b in d["blocks"]:
        if b["type"] == 1:
            continue
        for line in b["lines"]:
            for span in line["spans"]:
                text = span["text"]
                if not text.strip():
                    continue
                x0, y0, _, _ = span["bbox"]
                size = span["size"]
                font = span["font"]
                color = span["color"]
                colhex = "#%02x%02x%02x" % ((color >> 16) & 255, (color >> 8) & 255, color & 255)
                spans_html.append(
                    f'<span style="position:absolute;left:{x0:.1f}pt;top:{y0:.1f}pt;'
                    f'font-size:{size:.1f}pt;font-family:{html.escape(font)};color:{colhex};white-space:pre;">'
                    f'{html.escape(text)}</span>'
                )

    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        f'<body style="{bg_css}margin:0;padding:0;">'
        f'<div style="position:relative;width:{W:.1f}pt;height:{H:.1f}pt;">'
        + "".join(spans_html) +
        "</div></body></html>"
    )
