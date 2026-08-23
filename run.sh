#!/usr/bin/env bash
# ResumeCraft 启动脚本
set -e
cd "$(dirname "$0")"

# 优先使用项目内 venv
if [ -d "venv" ]; then
  PY=venv/bin/python
elif [ -d ".venv" ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

echo "=== ResumeCraft 启动 ==="
echo "Python: $($PY --version)"

# 检查依赖，缺失则安装
$PY -c "import fastapi" 2>/dev/null || {
  echo "安装依赖..."
  $PY -m pip install -r backend/requirements.txt
}

echo "启动服务: http://localhost:5015"
exec $PY -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 5015
