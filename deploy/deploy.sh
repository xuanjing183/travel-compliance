#!/usr/bin/env bash
# Travel Compliance Desk — CentOS Docker 一键部署
# 用法: cd /opt/travel-compliance-desk/deploy && sudo bash deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ─── Step 1: 检查 Docker ───────────────────────────────────────────
info "检查 Docker 环境..."

if ! command -v docker &> /dev/null; then
    warn "Docker 未安装，正在安装..."

    # 安装依赖
    if command -v dnf &> /dev/null; then
        dnf install -y dnf-utils device-mapper-persistent-data lvm2
        dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    elif command -v yum &> /dev/null; then
        yum install -y yum-utils device-mapper-persistent-data lvm2
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    else
        error "不支持的包管理器，请手动安装 Docker"
        exit 1
    fi

    systemctl enable --now docker
    info "Docker 安装完成"
fi

# 验证 docker compose
if ! docker compose version &> /dev/null; then
    warn "Docker Compose 插件未安装，正在安装..."
    if command -v dnf &> /dev/null; then
        dnf install -y docker-compose-plugin
    elif command -v yum &> /dev/null; then
        yum install -y docker-compose-plugin
    fi
fi

docker --version
docker compose version

# ─── Step 2: 检查 .env ─────────────────────────────────────────────
info "检查环境变量配置..."

if [ ! -f "$ENV_FILE" ]; then
    warn ".env 文件不存在，从模板创建..."
    cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
    echo ""
    error "请先编辑 $ENV_FILE 填写必要的环境变量（至少需要 DASHSCOPE_API_KEY）"
    error "编辑完成后重新运行: sudo bash deploy.sh"
    exit 1
fi

# 检查关键变量
if ! grep -q "^DASHSCOPE_API_KEY=sk-" "$ENV_FILE" 2>/dev/null; then
    warn ".env 中未检测到有效的 DASHSCOPE_API_KEY，LLM 功能可能不可用"
fi

# ─── Step 3: 创建运行时目录 ────────────────────────────────────────
info "创建运行时目录..."
mkdir -p "$PROJECT_ROOT/var/uploads"
mkdir -p "$PROJECT_ROOT/artifacts"

# ─── Step 4: 构建镜像 ──────────────────────────────────────────────
info "构建 Docker 镜像（首次构建约 2-5 分钟）..."
docker compose -f "$COMPOSE_FILE" build

# ─── Step 5: 启动容器 ──────────────────────────────────────────────
info "启动容器..."
docker compose -f "$COMPOSE_FILE" up -d

# ─── Step 6: 等待健康检查 ──────────────────────────────────────────
info "等待服务就绪..."
RETRIES=0
MAX_RETRIES=30
until [ "$RETRIES" -ge "$MAX_RETRIES" ]; do
    if docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | grep -q '"healthy"'; then
        break
    fi
    # Fallback: check if container is running and port is listening
    if docker compose -f "$COMPOSE_FILE" ps 2>/dev/null | grep -q "Up"; then
        if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
            break
        fi
    fi
    RETRIES=$((RETRIES + 1))
    sleep 2
done

# ─── Step 7: 验证结果 ──────────────────────────────────────────────
echo ""
if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    info "=== 部署成功 ==="
    echo ""
    echo "  服务地址:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'localhost'):8000"
    echo "  健康检查:  http://localhost:8000/api/health"
    echo ""
    echo "  常用命令:"
    echo "    查看日志:   docker compose -f $COMPOSE_FILE logs -f"
    echo "    重启服务:   docker compose -f $COMPOSE_FILE restart"
    echo "    停止服务:   docker compose -f $COMPOSE_FILE down"
    echo "    查看状态:   docker compose -f $COMPOSE_FILE ps"
    echo ""
else
    warn "容器已启动但健康检查未通过，查看日志排查："
    echo ""
    docker compose -f "$COMPOSE_FILE" logs --tail=30
    echo ""
    error "部署可能未完成，请检查上方日志"
    exit 1
fi
