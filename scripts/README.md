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

3. 之后一键同步：

```bash
bash scripts/sync_unraid.sh
```
