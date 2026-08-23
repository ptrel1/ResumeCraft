"""轻量 front matter + markdown 解析，兼容 Python 3.9 无第三方强依赖。

用 PyYAML（若有）解析 front matter；若无则回退为纯文本逐行解析。
避免 python-frontmatter 在 Python 3.9 上的 TypeGuard 兼容问题。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def loads(content: str) -> Tuple[Dict[str, Any], str]:
    """解析 '---' 包裹的 YAML front matter，返回 (metadata, body)。"""
    content = content.lstrip("\ufeff")  # 去除 BOM
    if not content.startswith("---"):
        return {}, content

    lines = content.splitlines(keepends=True)
    if len(lines) < 2:
        return {}, content

    # 找到闭合的 '---'
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, content

    meta_text = "".join(lines[1:end])
    body = "".join(lines[end + 1:])
    return _parse_meta(meta_text), body


def _parse_meta(text: str) -> Dict[str, Any]:
    """解析 front matter 文本为 dict。优先 YAML，回退简单 kv。"""
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    # 回退：简单 key: value 逐行解析
    meta: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.lower() in ("true", "false"):
                meta[k] = v.lower() == "true"
            elif re.match(r"^[0-9]+$", v):
                meta[k] = int(v)
            elif re.match(r"^[0-9]+\.[0-9]+$", v):
                meta[k] = float(v)
            else:
                meta[k] = v
    return meta
