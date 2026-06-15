# Deploy Scripts

1. 复制部署配置：

```bash
mkdir -p .deploy
cp .deploy.example.env .deploy/unraid.env
```

`sync_unraid.sh` 会生成使用 `network_mode: host` 的 unRAID Compose 配置，这是为了兼容
宿主机上的 qBittorrent WebUI、Cloudflare Tunnel 和代理访问。请只在受信任的个人
unRAID 主机上使用该脚本。脚本会同时配置 `no-new-privileges`、`cap_drop: ALL`、
只读根文件系统和 `/tmp` tmpfs，把 root 容器配合 host 网络的运行时权限收窄到
bot 当前需要的范围内。请保留 `.deploy/unraid.env` 中的显式确认：

```env
UNRAID_HOST_NETWORK_ACK=I_UNDERSTAND_HOST_NETWORK_IS_INTENTIONAL
```

2. 首次配置 SSH 免密：

```bash
bash scripts/setup_unraid_key.sh '你的unraid密码'
```

3. 如需自动发布 Docker Hub，复制 Docker Hub 配置模板：

```bash
cp .deploy.dockerhub.example.env .deploy/dockerhub.env
```

4. 安装 Git Hook，让 `main` 分支每次 `git commit` 后自动推送 GitHub、同步到 unRAID，并发布 Docker Hub：

```bash
bash scripts/install_git_hooks.sh
```

5. 本地提交或同步前，先跑统一验证：

```bash
bash scripts/validate.sh
```

6. 之后也可以随时手动一键同步：

```bash
bash scripts/sync_unraid.sh
```

7. 也可以随时手动发布 Docker Hub：

```bash
bash scripts/publish_dockerhub.sh
```
