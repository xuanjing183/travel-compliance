#!/usr/bin/env bash
# Live 服务操作员入口（AUTH-02）。
# 将 ./.env 加载进进程环境（HC-07：.env 由操作员加载，应用自身不读取、不入库），
# 然后按文档唯一启动命令启动服务。
# 用法：./start-live.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "[start-live] 缺少 .env；请先创建并填写：cp .env.example .env（或参照仓库根目录说明）" >&2
  exit 1
fi

set -a
. ./.env
set +a

if [ -x ./.venv/Scripts/travel-desk.exe ]; then
  exec ./.venv/Scripts/travel-desk.exe
fi
exec uv run travel-desk
