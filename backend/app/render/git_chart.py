"""本地 Git 贡献日历生成器：精准区分并统计 GitHub 与 Gitea 私有仓库的提交细分，生成支持双源展示的交互式热力图。"""
from __future__ import annotations

import datetime
import os
import subprocess
from collections import defaultdict
from pathlib import Path

# 排除非个人主力自研的超大第三方镜像/框架仓库（否则会刷出大量非本人提交）
# ⚠️ 重写时务必保留这些排除仓库，避免回归（如 deepseek-harness 单仓库 1.3 万提交）
EXCLUDE_REPOS = {"deepseek-harness", "deepseek-harness.git", "frp", "3x-ui", "cookiecloud"}


def _load_data_source_config() -> dict:
    """从 config/connectors.toml 读取数据源配置（真实路径/域名在本机配置文件，不随 git 推送）。

    返回 { github_base, gitea_base, gitea_host }；配置文件不存在则返回空字典，
    由调用方按空处理（不产生假数据）。
    """
    import sys
    # git_chart.py 位于 backend/app/render/，上溯 4 层到项目根 ResumeCraft/
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    cfg_path = project_root / "config" / "connectors.toml"
    if not cfg_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    git_cfg = raw.get("git", {})
    # 归属邮箱白名单：只统计这些邮箱作者提交（他人/框架提交会自动过滤）
    emails = git_cfg.get("your_emails", [])
    if not isinstance(emails, list):
        emails = [emails]
    return {
        "github_user": str(git_cfg.get("github_user", "") or ""),
        "github_base": str(git_cfg.get("github_base", "") or ""),
        "gitea_base": str(git_cfg.get("gitea_base", "") or ""),
        "gitea_host": str(git_cfg.get("gitea_host", "") or ""),
        "your_emails": [str(e).lower() for e in emails if str(e).strip()],
    }


def _fetch_github_contributions(username: str) -> dict[str, int]:
    """从 GitHub 官方贡献数据接口拉取指定用户的每日贡献（免鉴权，权威口径）。

    返回 { 'YYYY-MM-DD': count }；失败返回空字典。
    接口：github-contributions-api.jogruber.de/v4/<user>?y=last
    """
    import json
    import urllib.request

    if not username:
        return {}
    try:
        url = f"https://github-contributions-api.jogruber.de/v4/{username}?y=last"
        req = urllib.request.Request(url, headers={"User-Agent": "ResumeCraft/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out: dict[str, int] = {}
        for item in data.get("contributions", []):
            c = item.get("count", 0)
            if c > 0:
                out[item.get("date", "")] = c
        return out
    except Exception:
        return {}


def generate_local_git_chart_svg(
    repo_dirs: list[str | Path] | None = None,
    accent_color: str = "#3fb950",
    bg_color: str = "#151a22",
    text_color: str = "#8b949e",
    weeks: int = 52,
) -> str:
    """遍历本地 GitHub 与 Gitea 仓库，按 GitHub / Gitea 精准区分每日提交量。仓库根目录与 Gitea 域名读自 config/connectors.toml。"""
    cfg = _load_data_source_config()
    your_emails = set(cfg.get("your_emails") or [])
    github_commits: dict[str, int] = defaultdict(int)
    gitea_commits: dict[str, int] = defaultdict(int)
    daily_commits: dict[str, int] = defaultdict(int)
    
    seen_github: set[str] = set()
    seen_gitea: set[str] = set()

    # 1. GitHub 部分：直接取 GitHub 官方贡献数据（权威，免鉴权，不再扫描本地仓库）
    github_user = cfg.get("github_user") or ""
    gh_contrib = _fetch_github_contributions(github_user)
    for date_str, count in gh_contrib.items():
        if date_str:
            github_commits[date_str] += count
            daily_commits[date_str] += count

    # 2. 扫描 Gitea 服务端私有仓库（读自配置，未配置则为空；按归属邮箱筛选）
    gitea_base = Path(cfg.get("gitea_base") or "")
    if gitea_base.exists():
        for r in [p for p in gitea_base.glob("*.git") if p.name not in EXCLUDE_REPOS]:
            try:
                cmd = ["git", "-C", str(r), "log", "--since=1.year", "--format=%H %ad %ae", "--date=short"]
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
                for line in out.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        h, d, e = parts[0], parts[1], parts[2].lower()
                        if your_emails and e not in your_emails:
                            continue
                        if h not in seen_gitea and h not in seen_github:
                            seen_gitea.add(h)
                            gitea_commits[d] += 1
                            daily_commits[d] += 1
            except Exception:
                pass

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=weeks * 7)

    cell_size = 11
    cell_gap = 3
    col_width = cell_size + cell_gap
    row_height = cell_size + cell_gap

    svg_width = weeks * col_width + 40
    svg_height = 7 * row_height + 46

    cells_svg = []
    
    cur_date = start_date
    while cur_date <= today:
        col = (cur_date - start_date).days // 7
        row = (cur_date.weekday() + 1) % 7
        d_str = cur_date.strftime("%Y-%m-%d")
        cnt = daily_commits.get(d_str, 0)
        gh_cnt = github_commits.get(d_str, 0)
        gt_cnt = gitea_commits.get(d_str, 0)

        # 颜色分级
        if cnt == 0:
            fill = "#21262d"
            opacity = "0.6"
            desc = "无提交"
        elif cnt <= 3:
            fill = "#0e4429"
            opacity = "0.9"
            desc = f"{cnt} 次提交"
        elif cnt <= 8:
            fill = "#006d32"
            opacity = "1.0"
            desc = f"{cnt} 次提交"
        elif cnt <= 20:
            fill = "#26a641"
            opacity = "1.0"
            desc = f"{cnt} 次高频迭代"
        else:
            fill = "#39d353"
            opacity = "1.0"
            desc = f"{cnt} 次密集攻坚"

        x = col * col_width + 20
        y = row * row_height + 22

        cell_xml = (
            f'<rect class="cal-cell" x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" rx="2.5" '
            f'fill="{fill}" opacity="{opacity}" '
            f'data-date="{d_str}" data-count="{cnt}" data-github="{gh_cnt}" data-gitea="{gt_cnt}" data-desc="{desc}" />'
        )
        cells_svg.append(cell_xml)
        cur_date += datetime.timedelta(days=1)

    joined_cells = "".join(cells_svg)
    total_commits = sum(daily_commits.values())
    total_gh = sum(github_commits.values())
    total_gt = sum(gitea_commits.values())

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_width} {svg_height}" width="100%" height="{svg_height}" fill="none" class="git-cal-svg" data-total="{total_commits}" data-gh="{total_gh}" data-gt="{total_gt}">
  <style>
    .cal-cell {{ transition: stroke 0.1s ease, opacity 0.1s ease; cursor: pointer; }}
    .cal-cell:hover {{ stroke: #ffffff; stroke-width: 1.5px; opacity: 1 !important; }}
  </style>
  <g id="cellsGroup">
    {joined_cells}
  </g>
</svg>"""
    return svg_content


def get_git_calendar_data(weeks: int = 52) -> dict:
    """返回 Git 贡献日历的**结构化数据**（JSON 可序列化），供导出使用。

    数据源与热力图一致：GitHub 官方贡献 API + 本地 Gitea 扫描（按归属邮箱筛选）。
    返回 { total, github_total, gitea_total, days: [{date,count,github,gitea}, ...] }。
    """
    cfg = _load_data_source_config()
    your_emails = set(cfg.get("your_emails") or [])

    github_commits: dict[str, int] = defaultdict(int)
    gitea_commits: dict[str, int] = defaultdict(int)
    daily_commits: dict[str, int] = defaultdict(int)

    # GitHub 官方贡献
    gh = _fetch_github_contributions(cfg.get("github_user") or "")
    for d, c in gh.items():
        if d:
            github_commits[d] += c
            daily_commits[d] += c

    # 本地 Gitea 私库（按归属邮箱筛选，同源 commit 去重）
    seen_gitea: set[str] = set()
    gitea_base = Path(cfg.get("gitea_base") or "")
    if gitea_base.exists():
        for r in [p for p in gitea_base.glob("*.git") if p.name not in EXCLUDE_REPOS]:
            try:
                cmd = ["git", "-C", str(r), "log", "--since=1.year", "--format=%H %ad %ae", "--date=short"]
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
                for line in out.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 3:
                        h, d, e = parts[0], parts[1], parts[2].lower()
                        if your_emails and e not in your_emails:
                            continue
                        if h not in seen_gitea:
                            seen_gitea.add(h)
                            gitea_commits[d] += 1
                            daily_commits[d] += 1
            except Exception:
                pass

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=weeks * 7)
    days = []
    cur = start_date
    while cur <= today:
        ds = cur.strftime("%Y-%m-%d")
        days.append({
            "date": ds,
            "count": daily_commits.get(ds, 0),
            "github": github_commits.get(ds, 0),
            "gitea": gitea_commits.get(ds, 0),
        })
        cur += datetime.timedelta(days=1)

    return {
        "total": sum(daily_commits.values()),
        "github_total": sum(github_commits.values()),
        "gitea_total": sum(gitea_commits.values()),
        "days": days,
    }
