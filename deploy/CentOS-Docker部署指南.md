# CentOS Docker 部署指南

> 前提：已通过 SFTP 工具将本地 `travel-compliance-desk` 文件夹拖拽上传到服务器 `/opt/` 目录。

---

## 第 1 步：本地拖拽上传

用你的 SFTP 工具（WinSCP / MobaXterm / FinalShell / Xftp 等），连接服务器后：

- **本地路径**：`C:\Users\22316\Desktop\1\lc-course\travel-compliance-desk`
- **服务器路径**：`/opt/travel-compliance-desk`

直接拖拽整个文件夹到服务器的 `/opt/` 目录下即可。

> 可选：排除 `.venv`、`__pycache__`、`var/uploads` 等本地运行时产物以加快传输。不排除也不影响——Docker 构建时 `.dockerignore` 会自动过滤。

---

## 第 2 步：SSH 登录服务器

```bash
ssh root@服务器IP
```

---

## 第 3 步：安装 Docker

```bash
# 安装依赖工具
yum install -y yum-utils

# 添加 Docker 官方仓库
yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

# 安装 Docker 引擎 + Compose 插件
yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动 Docker 并设为开机自启
systemctl enable --now docker

# 验证安装
docker --version
docker compose version
```

两条命令都能正常输出版本号即表示安装成功。

---

## 第 4 步：配置环境变量

```bash
cd /opt/travel-compliance-desk

# 从模板创建 .env
cp .env.example .env

# 编辑 .env
vi .env
```

将 `.env` 内容改为如下（按你的实际情况填写）：

```env
# ===== 必填：通义千问 API Key =====
DASHSCOPE_API_KEY=sk-你的真实密钥

# ===== 服务模式（real+live：真实 LLM + 真实供应商）=====
TRAVEL_DESK_LLM_MODE=real
TRAVEL_DESK_PROVIDER_MODE=live

# ===== 模型选择（可选，默认即可）=====
TRAVEL_DESK_TEXT_MODEL=qwen3.8-max
TRAVEL_DESK_VISION_MODEL=qwen3.5-plus
TRAVEL_DESK_EMBED_MODEL=qwen3.7-text-embedding
TRAVEL_DESK_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ===== Live 供应商密钥（必填，否则酒店/天气功能不可用）=====
TRAVEL_DESK_TAVILY_API_KEY=你的Tavily密钥
TRAVEL_DESK_QWEATHER_API_HOST=你的和风天气API Host
TRAVEL_DESK_QWEATHER_API_KEY=你的和风天气API Key

# ===== 演示日期（可选）=====
DEMO_TODAY=2026-10-20
```

> `TRAVEL_DESK_HOST` 和 `TRAVEL_DESK_PORT` 不需要在这里设置，docker-compose.yml 里已经通过 `environment` 注入了 `0.0.0.0:8000`。

保存退出（vi 操作：按 `Esc` → 输入 `:wq` → 回车）。

---

## 第 5 步：开放防火墙端口

```bash
# 如果用的 firewalld（CentOS 7/8 默认）
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --reload

# 如果用的 iptables
iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
iptables-save > /etc/sysconfig/iptables
```

> **重要**：如果服务器在云上（阿里云/腾讯云/华为云等），还需要去云控制台的**安全组**里放行 8000 端口入方向，否则外网无法访问。

---

## 第 6 步：构建并启动（一键）

```bash
cd /opt/travel-compliance-desk/deploy
bash deploy.sh
```

脚本会自动完成以下操作：
1. 检测 Docker 环境（缺失则自动安装）
2. 检查 `.env` 文件是否存在
3. 创建运行时目录
4. 构建 Docker 镜像（首次约 2-5 分钟）
5. 启动容器
6. 等待健康检查通过
7. 输出服务地址

看到 `=== 部署成功 ===` 即表示完成。

---

## 第 7 步：验证服务

```bash
# 检查容器状态
docker compose ps

# 应该看到类似：
# NAME                      STATUS                   PORTS
# travel-compliance-desk    Up (healthy)             0.0.0.0:8000->8000/tcp

# 手动测试健康接口
curl http://localhost:8000/api/health

# 测试页面
curl -s http://localhost:8000/ | head -5
```

打开浏览器访问 `http://服务器IP:8000`，能看到"差旅合规工作台"页面即部署成功。

---

## 日常运维命令

所有命令都在 `/opt/travel-compliance-desk/deploy` 目录下执行：

```bash
cd /opt/travel-compliance-desk/deploy

# 查看实时日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务（数据不丢失）
docker compose down

# 启动服务
docker compose up -d

# 查看容器状态
docker compose ps

# 查看资源占用
docker stats travel-compliance-desk

# 进入容器内部调试
docker exec -it travel-compliance-desk bash
```

---

## 更新代码

本地修改代码后，重新拖拽上传并重建：

1. **本地**：用 SFTP 工具重新拖拽 `travel-compliance-desk` 文件夹到服务器 `/opt/`，覆盖旧文件
2. **服务器**：

```bash
cd /opt/travel-compliance-desk/deploy
docker compose build --no-cache
docker compose up -d
```

> 数据存储在 Docker Volume 中（`desk-data`、`desk-artifacts`），重建容器不会丢失。
> **千万不要执行** `docker compose down -v`，`-v` 会删除数据卷。

---

## 故障排查

### 镜像构建失败

```bash
# 查看完整构建日志
docker compose build --no-cache 2>&1 | tail -50
```

常见原因：
- 网络问题导致 pip 安装失败 → 多试几次，或在 Dockerfile 中加国内镜像源（见附录 A）
- Python 版本不匹配 → 确认基础镜像是 python:3.11

### 容器启动后立即退出

```bash
# 查看容器日志
docker compose logs --tail=100
```

常见原因：
- `.env` 文件缺失或格式错误
- `var/` 目录权限问题
- 端口 8000 被占用 → `lsof -i :8000` 查看

### 健康检查一直不通过

```bash
# 手动进入容器检查
docker exec -it travel-compliance-desk bash
curl http://localhost:8000/api/health
```

### 端口被占用

```bash
# 查看占用进程
lsof -i :8000

# 解决：修改 docker-compose.yml 的端口映射
# 把 "8000:8000" 改为 "8080:8000"，然后重新 up -d
```

### 页面能打开但 LLM 功能报错

检查 `.env` 中的 `DASHSCOPE_API_KEY` 是否正确：
```bash
grep DASHSCOPE_API_KEY /opt/travel-compliance-desk/.env
```

---

## 附录 A：pip 国内加速（构建慢时）

如果 Docker 构建时 pip install 很慢，编辑 Dockerfile，在 `pip install` 行加镜像源：

```dockerfile
RUN pip install --user --no-cache-dir -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
```

然后重建：
```bash
docker compose build --no-cache
```

---

## 附录 B：Nginx 反向代理（可选）

如果想用 80 端口或域名访问：

```bash
# 安装 Nginx
yum install -y nginx

# 写配置
cat > /etc/nginx/conf.d/travel-desk.conf << 'EOF'
server {
    listen 80;
    server_name 你的域名或IP;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
EOF

# 启动 Nginx
nginx -t
systemctl enable --now nginx

# 同时放行 80 端口
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --reload
```

---

## 附录 C：数据备份与恢复

### 备份

```bash
# 停止服务（可选，确保数据一致性）
docker compose down

# 备份数据卷
docker run --rm \
  -v travel-compliance-desk_desk-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/desk-data-backup-$(date +%Y%m%d).tar.gz -C /data .

# 重新启动
docker compose up -d
```

### 恢复

```bash
# 停止服务
docker compose down

# 解压备份到数据卷
docker run --rm \
  -v travel-compliance-desk_desk-data:/data \
  -v $(pwd):/backup \
  alpine sh -c "cd /data && tar xzf /backup/desk-data-backup-XXXXXXXX.tar.gz"

# 启动
docker compose up -d
```

---

## 文件清单

```
travel-compliance-desk/
├── deploy/
│   ├── Dockerfile                      # 镜像构建定义
│   ├── docker-compose.yml              # 容器编排配置
│   ├── deploy.sh                       # 一键部署脚本
│   └── CentOS-Docker部署指南.md        # 本文档
├── .dockerignore                       # Docker 构建排除规则
├── .env                                # 环境变量（需手动创建，不提交 Git）
├── .env.example                        # 环境变量模板
├── src/                                # 源代码
├── tests/fixtures/                     # 制度文档/供应商数据
└── var/                                # 数据库（构建时打包进镜像）
    ├── app.sqlite                      # 演示数据
    └── checkpoints.sqlite              # 工作流检查点
```
