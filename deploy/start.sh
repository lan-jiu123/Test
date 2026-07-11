#!/bin/bash
# ============================================================
# 设备检修AI系统 - 一键部署启动脚本
# 适配：银河麒麟高级服务器版 V10/V11 + LoongArch架构
#
# 用法：
#   bash deploy/start.sh           # 默认云端模式（LongCat API）
#   bash deploy/start.sh --cloud   # 云端模式（显式）
#   bash deploy/start.sh --offline # 离线模式（Ollama 本地模型）
#   bash deploy/start.sh --both    # 双模式（可在 .env 中切换）
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

MODE="${1:-cloud}"
INSTALL_LOG="$PROJECT_DIR/install.log"

echo "=========================================="
echo "  设备检修AI系统 - 部署启动脚本 v2.0"
echo "  目标环境：银河麒麟 V10/V11 + LoongArch"
echo "=========================================="
echo ""

# ---- 颜色定义 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 1. 系统环境检测 ----
echo "=========================================="
echo "  [步骤 1/7] 系统环境检测"
echo "=========================================="

ARCH=$(uname -m 2>/dev/null || echo "unknown")
CPU_CORES=$(nproc 2>/dev/null || echo "4")
MEM_TOTAL=$(free -m 2>/dev/null | awk '/Mem:/{print $2}' || echo "8192")
DISK_AVAIL=$(df -BG . 2>/dev/null | awk 'NR==2{print $4}' | sed 's/G//' || echo "256")

log_info "CPU架构: $ARCH"
log_info "CPU核心数: $CPU_CORES (要求: 四核及以上)"
log_info "内存: ${MEM_TOTAL}MB (要求: 8GB以上)"
log_info "可用磁盘: ${DISK_AVAIL}GB (要求: 256GB以上)"

# 检测操作系统
if [ -f /etc/kylin-release ]; then
    KYLIN_VERSION=$(cat /etc/kylin-release | head -1)
    log_ok "检测到银河麒麟操作系统: $KYLIN_VERSION"
elif [ -f /etc/os-release ]; then
    . /etc/os-release
    log_info "操作系统: $NAME $VERSION"
fi

# 检查硬件要求
if [ "$ARCH" != "loongarch64" ] && [ "$ARCH" != "aarch64" ]; then
    log_warn "当前架构为 $ARCH，非 LoongArch 架构"
    log_warn "竞赛要求在 LoongArch 架构上运行"
fi

if [ "$CPU_CORES" -lt 4 ]; then
    log_warn "CPU核心数不足4核，可能影响性能"
fi

if [ "$MEM_TOTAL" -lt 8000 ]; then
    log_warn "内存小于8GB，可能影响大模型运行"
fi

# 检查 Python
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    log_info "Python版本: $PYTHON_VERSION"
    if [[ ! "$PYTHON_VERSION" =~ Python\ 3\.(10|11|12) ]]; then
        log_warn "推荐 Python 3.10-3.12，当前版本可能存在兼容性问题"
    fi
else
    log_error "未安装 Python3，请先安装"
    exit 1
fi

echo ""

# ---- 2. 安装系统依赖 ----
echo "=========================================="
echo "  [步骤 2/7] 安装系统依赖"
echo "=========================================="

install_package() {
    local pkg="$1"
    if command -v "$pkg" &> /dev/null; then
        log_ok "$pkg 已安装"
        return 0
    fi
    log_info "安装 $pkg..."
    if command -v yum &> /dev/null; then
        sudo yum install -y "$pkg" >> "$INSTALL_LOG" 2>&1 || true
    elif command -v apt &> /dev/null; then
        sudo apt install -y "$pkg" >> "$INSTALL_LOG" 2>&1 || true
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y "$pkg" >> "$INSTALL_LOG" 2>&1 || true
    fi
    if command -v "$pkg" &> /dev/null; then
        log_ok "$pkg 安装成功"
    else
        log_warn "$pkg 安装失败（可能不影响运行）"
    fi
}

install_package "curl"
install_package "git"

echo ""

# ---- 3. 创建虚拟环境 ----
echo "=========================================="
echo "  [步骤 3/7] 创建虚拟环境"
echo "=========================================="

cd "$PROJECT_DIR"

if [ -d "venv" ]; then
    log_ok "虚拟环境已存在"
else
    log_info "创建虚拟环境..."
    python3 -m venv venv
    log_ok "虚拟环境创建成功"
fi

source venv/bin/activate
log_ok "虚拟环境已激活"

echo ""

# ---- 4. 安装 Python 依赖 ----
echo "=========================================="
echo "  [步骤 4/7] 安装 Python 依赖"
echo "=========================================="

log_info "升级 pip..."
pip install --upgrade pip >> "$INSTALL_LOG" 2>&1

log_info "安装项目依赖..."

if [ "$ARCH" = "loongarch64" ] || [ -f /etc/kylin-release ]; then
    log_info "检测到 LoongArch/银河麒麟，使用龙芯镜像源..."
    pip install --no-cache-dir -r backend/requirements.txt \
        -i https://pypi.loongnix.cn/loongnix/pypi/simple \
        --trusted-host pypi.loongnix.cn >> "$INSTALL_LOG" 2>&1 || \
    pip install --no-cache-dir -r backend/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple >> "$INSTALL_LOG" 2>&1 || \
    pip install --no-cache-dir -r backend/requirements.txt --use-pep517 >> "$INSTALL_LOG" 2>&1
else
    pip install --no-cache-dir -r backend/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple >> "$INSTALL_LOG" 2>&1 || \
    pip install --no-cache-dir -r backend/requirements.txt >> "$INSTALL_LOG" 2>&1
fi

log_ok "Python 依赖安装完成"

echo ""

# ---- 5. 配置环境变量 ----
echo "=========================================="
echo "  [步骤 5/7] 配置环境变量"
echo "=========================================="

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        log_ok "已创建 .env 配置文件"
    fi
fi

# 根据部署模式配置 LLM_BACKEND
if [ -f ".env" ]; then
    case "$MODE" in
        --offline)
            log_info "配置为离线模式（Ollama 本地模型）..."
            if grep -q "^LLM_BACKEND=" .env 2>/dev/null; then
                sed -i 's/^LLM_BACKEND=.*/LLM_BACKEND=ollama/' .env
            else
                echo "LLM_BACKEND=ollama" >> .env
            fi
            log_ok "已配置为离线模式"
            ;;
        --cloud)
            log_info "配置为云端模式（LongCat API）..."
            if grep -q "^LLM_BACKEND=" .env 2>/dev/null; then
                sed -i 's/^LLM_BACKEND=.*/LLM_BACKEND=longcat/' .env
            else
                echo "LLM_BACKEND=longcat" >> .env
            fi
            log_ok "已配置为云端模式"
            ;;
        --both|*)
            log_info "双模式配置（默认 LongCat，可在 .env 中切换为 ollama）"
            ;;
    esac
fi

# 初始化数据目录
mkdir -p data/logs data/pdfs data/images
log_ok "数据目录已创建"

echo ""

# ---- 6. 准备前端文件 ----
echo "=========================================="
echo "  [步骤 6/7] 准备前端文件"
echo "=========================================="

if [ -d "frontend/dist" ] && [ "$(ls -A frontend/dist 2>/dev/null)" ]; then
    log_ok "前端构建产物存在"
    if [ -d "deploy/frontend" ]; then
        rm -rf deploy/frontend/dist
        cp -r frontend/dist deploy/frontend/
        log_ok "前端文件已复制到 deploy/frontend/dist"
    fi
else
    log_warn "前端构建产物不存在，请运行: cd frontend && npm ci && npm run build"
fi

echo ""

# ---- 7. 配置启动服务 ----
echo "=========================================="
echo "  [步骤 7/7] 配置启动服务"
echo "=========================================="

# 创建 systemd 服务文件
sudo tee /etc/systemd/system/equipment-maintenance.service > /dev/null << EOF
[Unit]
Description=Equipment Maintenance AI System
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR/backend
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 重新加载 systemd
sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl enable equipment-maintenance.service 2>/dev/null || true
log_ok "systemd 服务已配置"

echo ""

# ---- 启动服务 ----
log_info "启动服务..."
cd "$PROJECT_DIR/backend"

uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | tee ../data/logs/api.log &
API_PID=$!
echo $API_PID > ../data/logs/api.pid
log_ok "后端PID: $API_PID"

# 等待后端就绪（健康检查）
echo ""
echo "等待后端就绪..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        log_ok "后端已就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log_warn "后端未在30秒内就绪"
    fi
    sleep 1
done

# ---- Ollama 检测（离线模式）----
echo ""
echo "=========================================="
echo "  [可选] Ollama 本地大模型检测"
echo "=========================================="

if command -v ollama &> /dev/null; then
    log_ok "Ollama 已安装"
    echo ""
    echo "  可用模型列表："
    ollama list 2>/dev/null | head -15 || echo "    (暂无已下载模型)"
    echo ""
    echo "  常用命令："
    echo "    ollama pull qwen2.5:7b    # 下载通义千问模型"
    echo "    ollama run qwen2.5:7b     # 运行模型"
else
    log_info "Ollama 未安装，如需离线部署请执行："
    echo ""
    echo "    curl -fsSL https://ollama.com/install.sh | sh"
    echo "    ollama pull qwen2.5:7b"
    echo ""
fi

# ---- 部署完成 ----
echo ""
echo "=========================================="
echo ""
echo -e "  ${GREEN}✓ 部署完成！${NC}"
echo ""
echo "=========================================="
echo ""
echo "  访问地址："
echo "    前端界面:  http://localhost:8000"
echo "    API文档:   http://localhost:8000/docs"
echo "    健康检查:  http://localhost:8000/health"
echo ""
echo "  管理命令："
echo "    sudo systemctl status equipment-maintenance  # 查看服务状态"
echo "    sudo systemctl restart equipment-maintenance  # 重启服务"
echo "    sudo journalctl -u equipment-maintenance -f   # 查看日志"
echo ""
echo "  配置说明："
echo "    配置文件: $PROJECT_DIR/.env"
echo "    数据目录: $PROJECT_DIR/data/"
echo "    安装日志: $INSTALL_LOG"
echo ""

# 最终健康检查
sleep 2
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "  ${GREEN}✓ 后端服务运行正常${NC}"
else
    echo -e "  ${YELLOW}⚠ 后端服务可能未正常运行，请检查配置${NC}"
fi

echo ""
echo "=========================================="

# 捕获退出信号，清理子进程
cleanup() {
    echo ""
    echo "正在停止服务..."
    kill -TERM $API_PID 2>/dev/null
    for i in $(seq 1 5); do
        kill -0 $API_PID 2>/dev/null || break
        sleep 1
    done
    kill -9 $API_PID 2>/dev/null
    rm -f ../data/logs/api.pid
    echo "服务已停止"
    exit 0
}

trap cleanup SIGINT SIGTERM

# 等待子进程
wait
