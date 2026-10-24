# Docker 镜像加速配置（国内服务器）

## 问题
构建时报错：`dial tcp 157.240.15.8:443: i/o timeout`
原因：Docker Hub 在国内访问不稳定或被墙。

## 解决方案：配置镜像加速器

### 方法 1：阿里云镜像加速（推荐）

1. 登录阿里云控制台，进入"容器镜像服务"
2. 左侧菜单"镜像加速器"，复制你的专属加速地址（形如 `https://xxxxx.mirror.aliyuncs.com`）
3. 在服务器上配置：

```bash
# 创建或编辑 Docker daemon 配置文件
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF'
{
  "registry-mirrors": [
    "https://xxxxx.mirror.aliyuncs.com",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
EOF

# 重启 Docker
systemctl daemon-reload
systemctl restart docker

# 验证配置
docker info | grep -A 5 "Registry Mirrors"
```

4. 重新构建：
```bash
cd /opt/travel-compliance-desk/deploy
bash deploy.sh
```

### 方法 2：如果没有阿里云账号

直接使用公共镜像源：

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF'
{
  "registry-mirrors": [
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com",
    "https://docker.mirrors.ustc.edu.cn"
  ]
}
EOF

systemctl daemon-reload
systemctl restart docker
```

然后重新执行 `bash deploy.sh`。

### 方法 3：手动拉取镜像（如果加速器也不稳定）

```bash
# 尝试手动拉取基础镜像
docker pull python:3.11-slim

# 如果成功，再执行构建
cd /opt/travel-compliance-desk/deploy
docker compose build

# 如果拉取也失败，继续配置加速器
```

## 验证

配置完成后，`docker info` 应该显示：
```
Registry Mirrors:
 https://xxxxx.mirror.aliyuncs.com/
 https://hub-mirror.c.163.com/
```

然后重新构建应该能在 1-2 分钟内完成。
