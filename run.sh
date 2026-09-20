#!/usr/bin/env bash
#
# LLM Gateway 启动脚本
#
#   ./run.sh                      # 默认 127.0.0.1:8000
#   ./run.sh --port 9000          # 覆盖端口（其余参数原样透传给 uvicorn）
#   ./run.sh --reload             # 开发模式，改代码自动重启
#   LLM_GW_HOST=0.0.0.0 ./run.sh  # 局域网可访问
#
# agent 接口（/v1/tasks*）的口令来自 .env 或环境变量 LLM_GW_AGENT_PASSWORD；
# 未设置时不强制，设置了就必须带 Authorization: Bearer <password>。
#
# 用 venv 里的 python 绝对路径启动，而不是裸 `python3`/`uvicorn`：
# 机器上装了多个 Python，PATH 在不同终端里顺序不同，裸命令会随机撞上
# 没有依赖的那个解释器（表现为 ModuleNotFoundError: uvicorn / aiosqlite）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT/.venv/bin/python"

HOST="${LLM_GW_HOST:-127.0.0.1}"
PORT="${LLM_GW_PORT:-8000}"

if [[ ! -x "$VENV_PY" ]]; then
  cat >&2 <<EOF
未找到虚拟环境 .venv，请先创建：

  cd "$ROOT"
  /opt/anaconda3/bin/python3 -m venv .venv
  .venv/bin/pip install -e ".[dev]"
EOF
  exit 1
fi

# 密钥等敏感配置放 .env（该文件不入版本库），不写进本脚本。
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

cd "$ROOT"

# 调用方可能用 --port 直接透传给 uvicorn，横幅要跟着变，否则提示的地址是错的。
for ((i = 1; i <= $#; i++)); do
  if [[ "${!i}" == "--port" ]]; then
    j=$((i + 1))
    PORT="${!j}"
  fi
done

echo "LLM Gateway → http://${HOST}:${PORT}   (Ctrl+C 停止)"
exec "$VENV_PY" -m uvicorn llm_gw.runtime:create_runtime_app \
  --factory --host "$HOST" --port "$PORT" "$@"
