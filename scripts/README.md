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

3. 安装 Git Hook，让 `main` 分支每次 `git commit` 后自动推送 GitHub 并同步到 unRAID：

```bash
bash scripts/install_git_hooks.sh
```

4. 之后也可以随时手动一键同步：

```bash
bash scripts/sync_unraid.sh
```
