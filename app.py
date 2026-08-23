#!/usr/bin/env python3
"""ResumeCraft 服务入口，供 supervisor / postsup 直接拉起。"""
import os
import sys
from pathlib import Path
import uvicorn

# 确保 backend 目录在 sys.path 中
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=5015, app_dir=str(ROOT_DIR / "backend"))
