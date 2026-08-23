"""指标存储与缓存管理器：定时拉取多平台客观数据并持久化到本地 JSON。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from ..connectors import fetch_all_metrics

CACHE_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "metrics" / "cache.json"
CACHE_TTL = 300  # 缓存 5 分钟


def get_cached_metrics(force_refresh: bool = False) -> Dict[str, Any]:
    """获取客观量化指标（优先读取本地有效缓存，超时则自动拉取刷新）。"""
    if not force_refresh and CACHE_FILE.exists():
        try:
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            ts = raw.get("_cached_at", 0)
            if time.time() - ts < CACHE_TTL:
                return raw.get("data", {})
        except Exception:
            pass

    # 重新拉取
    data = fetch_all_metrics()
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_cached_at": int(time.time()),
            "data": data
        }
        CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return data
