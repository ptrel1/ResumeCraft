"""PDF 导出：使用 Playwright (Chromium) 将 HTML 渲染为 PDF。

采用 playwright 异步 API（async_api），在 FastAPI async 端点中直接 await 调用。

与浏览器预览完全一致，可编程控制背景色、页边距、纸张。
首次使用需 `playwright install chromium` 下载内核。
"""
from __future__ import annotations

from pathlib import Path


async def html_to_pdf(html_content: str, out_path: str, *,
                      page_size: str = "A4",
                      margin_mm: float = 10,
                      print_background: bool = True,
                      base_url: str = "http://127.0.0.1:5015/") -> Path:
    """渲染 HTML → PDF（异步，playwright async API）。

    会在 HTML 的 <head> 注入 <base>，使根相对资源（/uploads 头像、/static CSS、
    /api/git/chart.svg 热力图）能被 Chromium 正确解析，避免导出后头像/样式缺失、排布错乱。
    """
    from playwright.async_api import async_playwright

    # 注入 <base>：让所有根相对资源定位到服务本身
    if "<base" not in html_content:
        html_content = html_content.replace(
            "<head>", f'<head><base href="{base_url}">', 1
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        # A4 视口（210mm × 297mm @96dpi ≈ 794×1123px），保证排版与 A4 一致
        await page.set_viewport_size({"width": 794, "height": 1123})
        await page.set_content(html_content, wait_until="networkidle")
        # 等待网络资源/字体加载完，避免头像、热力图、样式缺失
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        margin = f"{margin_mm}mm"
        await page.pdf(
            path=out_path,
            format=page_size,
            margin={"top": margin, "bottom": margin, "left": margin, "right": margin},
            print_background=print_background,
        )
        await browser.close()
    return Path(out_path)
