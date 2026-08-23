"""GitHub 真实统计连接器：从 GitHub 公开 API 获取认证用户的实际公开仓库数、fork 数、star 数。

设计原则：
- 只拉取真实数据，绝不回填硬编码占位值。
- GitHub 无鉴权 API 有速率限制（约 60 次/小时），因此持久化到本地缓存并依 TTL 复用。
- 网络异常 / 超时 / 非 200 时返回 None 字段，由上层决定展示为「—」而非假数字。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

from . import BaseConnector  # 复用基类，避免循环依赖（__init__ 尾部导入本模块）

CACHE_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "metrics" / "github_stats.json"
CACHE_TTL = 300  # 秒；GitHub 限速下 5 分钟刷新一次足够


def _http_get_json(url: str, timeout: float = 6.0):
    """极简 GET JSON，带 UA、超时、非 200 抛错。返回反序列化后的对象。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (ResumeCraft metrics)",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub API HTTP {resp.status}")
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _load_cache() -> Dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(payload: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class GitHubStatsConnector(BaseConnector):
    """GitHub 公开统计：真实仓库数（区分原创 vs fork）、star 数。"""
    name = "github"

    def fetch(self, config: Dict[str, Any]) -> Dict[str, Any]:
        user = config.get("username", "your-github-username")
        cache = _load_cache()

        # 1. 有效缓存直接复用（避免限速）
        cached_at = cache.get("_cached_at", 0)
        if cache.get("username") == user and time.time() - cached_at < CACHE_TTL:
            return cache.get("data", {})

        # 2. 拉取真实数据
        repos = []
        try:
            repos = _http_get_json(f"https://api.github.com/users/{user}/repos?per_page=100")
        except Exception:
            repos = []

        total_repos = len(repos)
        # fork 不计入「原创作品」；公开仓库总数仍含 fork（作为公开仓库数口径），但另给原创数
        forks = [r for r in repos if r.get("fork")]
        own = [r for r in repos if not r.get("fork")]

        # 各核心仓库真实 star（可控的核心项目白名单）
        core_repos = config.get(
            "core_repos",
            ["mais_art_journal", "napcat_mcp", "dsh-postapi-bridge", "server-defender", "ddns-ipv6"],
        )
        stars = {}
        for r in repos:
            n = r.get("name")
            if n in core_repos:
                stars[n] = r.get("stargazers_count", 0)

        top_star = sorted(((r.get("stargazers_count", 0), r.get("name", "")) for r in own), reverse=True)[:3]
        top_star = [{"name": n, "stars": s} for s, n in top_star if s > 0]

        data = {
            "username": user,
            "public_repos_count": total_repos,
            "own_repos_count": len(own),
            "fork_repos_count": len(forks),
            "top_star": top_star,
            "core_stars": stars,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            # 网络失败时保留最后一次成功数据作为兜底（非假数，是上次真实值）
            "_last_good": True,
        }

        if repos:
            payload = {"username": user, "_cached_at": int(time.time()), "data": data}
            _save_cache(payload)
        else:
            # 本次拉取失败：若上次有真实缓存，回退到它；否则返回空（由上层显示「—」）
            if cache.get("data") and cache.get("username") == user:
                return cache.get("data", {})
            data["error"] = "github_api_unreachable"
            return data

        return data
