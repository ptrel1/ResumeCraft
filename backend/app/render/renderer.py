"""渲染器：将结构化 Resume 数据 + HTML 模板 → 完整简历 HTML。

模板放在 backend/templates/{template}.html，使用 Jinja2 语法。
模板内通过 {{ resume }}、{{ resume.contact }}、{{ resume.sections }} 等访问数据，
CSS 变量可通过 front matter 的 theme 覆盖。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from ..connectors.storage import get_cached_metrics
from ..models import Resume

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def render_inline_md(text: str | None) -> Markup:
    """安全解析行内 Markdown 语法：图片 (![alt](url))、链接 ([text](url))、加粗 (**)、斜体 (*)、行内代码 (`) 等。"""
    if not text:
        return Markup("")
    # 先做 HTML 字符转义，防 XSS / 格式错乱
    s = html.escape(str(text))
    
    # 1. 针对 Git 贡献日历 /api/git/chart.svg 采用 GitHub 官方同款架构（卡片头 + 原生网格 + 顶层毛玻璃 Tooltip）
    def _img_replacer(match):
        alt = match.group(1)
        url = match.group(2)
        if "/api/git/chart.svg" in url:
            try:
                from .git_chart import generate_local_git_chart_svg
                raw_svg = generate_local_git_chart_svg()
                # 从 SVG 提取真实提交总数，动态替换卡片头文案（不再写死静态数字）
                _total = "0"
                _m = __import__("re").search(r'data-total="(\d+)"', raw_svg)
                if _m:
                    _total = _m.group(1)
                _total_fmt = f"{int(_total):,}" if _total.isdigit() else _total
                card_html = f"""
                <div class="git-chart-card">
                  <div class="git-chart-head">
                    <span class="summary-title">⚡ 真实 Git 贡献热力图 (GitHub + Gitea 双源)</span>
                    <span class="status-pill">累计 {_total_fmt} 次自研提交</span>
                  </div>
                  <div class="git-chart-svg-wrap">
                    {raw_svg}
                  </div>
                  <div class="git-pop-tooltip"></div>
                </div>
                """
                return card_html
            except Exception:
                pass
        return f'<img class="md-img" src="{url}" alt="{alt}" loading="lazy" />'

    s = re.sub(
        r"!\[([^\]]*)\]\(((\/|https?:\/\/)[^\s\)\"\']+)\)",
        _img_replacer,
        s
    )
    
    # 2. 超链接语法：[text](url) -> 智能识别 GitHub / DeepSeek / 硅基流动等品牌并前置矢量微标
    # 采用原生内联 SVG，直接使用 currentColor 完美自适应暗黑/浅色字体颜色，彻底告别 img 滤镜失效
    github_inline_svg = '<svg class="inline-brand-icon github-brand-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>'

    def _link_replacer(match):
        label = match.group(1)
        url = match.group(2)
        icon_html = ""
        url_lower = url.lower()
        if "github.com" in url_lower:
            icon_html = github_inline_svg
        elif "deepseek.com" in url_lower:
            icon_html = '<img class="inline-brand-icon" src="/static/icons/deepseek.svg" alt="DeepSeek" />'
        elif "siliconflow" in url_lower:
            icon_html = '<img class="inline-brand-icon" src="/static/icons/siliconflow.svg" alt="SiliconFlow" />'
        return f'<a class="md-link" href="{url}" target="_blank" rel="noopener">{icon_html}{label}</a>'

    s = re.sub(
        r"\[([^\]]+)\]\(((\/|https?:\/\/)[^\s\)\"\']+)\)",
        _link_replacer,
        s
    )
    
    # 3. **加粗** 或 __加粗__ -> <b>...</b>
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s)
    
    # 4. `代码` -> <code>...</code>
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    
    # 5. *斜体* 或 _斜体_ (避免破坏已转换的标签)
    s = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", s)
    
    return Markup(s)


def _theme_css(theme: dict) -> str:
    """把 front matter 的 theme 字典转成 CSS :root 变量覆盖。"""
    if not theme:
        return ""
    lines = [":root{"]
    for k, v in theme.items():
        lines.append(f"  --{k}: {v};")
    lines.append("}")
    return "\n".join(lines)


def render_resume(resume: Resume, template_name: str | None = None) -> str:
    """渲染完整简历 HTML。模板名缺省用 resume.meta.template。自动注入全局 metrics 指标。"""
    name = template_name or resume.meta.template
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["md_inline"] = render_inline_md
    tpl = env.get_template(f"{name}.html")
    
    # 获取多平台真实量化指标
    metrics = get_cached_metrics()
    
    return tpl.render(
        resume=resume,
        theme_css=_theme_css(resume.meta.theme),
        meta=resume.meta,
        metrics=metrics,
    )


def inline_css(html_content: str) -> str:
    """将 HTML 内的 <style> 转为内联（简化实现，供需要时使用）。"""
    # 保留原样，暂不做深度内联；Playwright 支持外部/内嵌样式表
    return html_content
