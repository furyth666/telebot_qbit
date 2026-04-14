# Deploy Scripts

1. 复制部署配置：

```bash
mkdir -p .deploy
cp .deploy.example.env .deploy/unraid.env
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

5. 之后也可以随时手动一键同步：

```bash
bash scripts/sync_unraid.sh
```

6. 也可以随时手动发布 Docker Hub：

```bash
bash scripts/publish_dockerhub.sh
```
