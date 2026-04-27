# qBittorrent Telegram Bot

一个给个人使用的 Telegram Bot，运行在 Docker 中，通过 qBittorrent Web API 查询和管理下载任务。

## 功能

- `/status` 查看 qBittorrent 整体状态
- `/list` 查看最近 10 个任务
- `/active` 查看活跃任务
- `/pause <hash>` 暂停任务
- `/resume <hash>` 恢复任务
- `/delete <hash>` 删除任务但保留文件
- `/deletefiles <hash>` 删除任务和文件
- `/add <magnet链接>` 添加下载
- `/retryjav <hash>` 重新执行 JAV 分类和文件筛选

## 使用方式

1. 创建 Telegram Bot，拿到 `TELEGRAM_BOT_TOKEN`
2. 获取你自己的 Telegram 用户 ID，并填入 `TELEGRAM_ALLOWED_USER_IDS`
3. 在 qBittorrent 中开启 WebUI，并确认账号密码可用
4. 复制环境变量模板：

```bash
cp .env.example .env
```

5. 修改 `.env`
6. 本地启动：

```bash
docker compose up -d --build
```

如果你在 unRAID 上使用 Compose Manager，建议把 compose 项目放在：

```text
/boot/config/plugins/compose.manager/projects/qbit-telegram-bot/compose.yaml
```

把运行数据放在：

```text
/mnt/user/appdata/qbit-telegram-bot
```

推荐直接使用 Docker Hub 镜像：

```yaml
services:
  qbit-telegram-bot:
    image: furyth666/qbit-telegram-bot:latest
    container_name: qbit-telegram-bot
    restart: unless-stopped
    network_mode: host
    env_file:
      - /mnt/user/appdata/qbit-telegram-bot/.env
    volumes:
      - /mnt/user/appdata/qbit-telegram-bot/data:/app/data
```

## 环境变量

- `TELEGRAM_BOT_TOKEN`: Telegram 机器人 token
- `TELEGRAM_ALLOWED_USER_IDS`: 允许访问的 Telegram 用户 ID，多个用逗号分隔
- `TELEGRAM_MODE`: Telegram 接收模式，`polling` 或 `webhook`
- `QBIT_BASE_URL`: qBittorrent WebUI 地址
- `QBIT_USERNAME`: qBittorrent 用户名
- `QBIT_PASSWORD`: qBittorrent 密码
- `BOT_LOG_LEVEL`: 日志等级，默认 `INFO`
- `JAV_CATEGORY_NAME`: JAV 自动分类名称，默认 `JAV`
- `JAV_NAME_REGEX`: JAV 标题匹配规则
- `JAV_LARGE_FILE_THRESHOLD_GB`: JAV 文件筛选阈值，默认 `1`
- `MAGNET_UPLOAD_LIMIT_KIB`: magnet 上传限速，默认 `30`
- `STATE_FILE_PATH`: bot 持久化状态文件，默认 `data/bot_state.json`
- `JELLYFIN_BASE_URL`: Jellyfin API 地址，用于检查是否已有同番号短片
- `JELLYFIN_PUBLIC_BASE_URL`: 返回给 Telegram 的 Jellyfin 内网访问地址
- `JELLYFIN_API_KEY`: Jellyfin API Key
- `JELLYFIN_DUPLICATE_DELETE_ENABLED`: 是否启用 Jellyfin 重复检查
- `JELLYFIN_DUPLICATE_GRACE_HOURS`: 同番号再次添加的保留窗口，默认 `3`
- `WATCHDOG_ENABLED`: 是否启用 bot 自检，默认 `true`
- `WATCHDOG_INTERVAL_SECONDS`: 自检间隔，默认 `300`
- `WATCHDOG_MAX_FAILURES`: 连续失败多少次后退出并交给 Docker 重启，默认 `3`
- `WEBHOOK_BASE_URL`: webhook 公网 HTTPS 地址，例如 `https://qbit-bot.furyth666.com`
- `WEBHOOK_LISTEN_HOST`: webhook 本地监听地址，默认 `0.0.0.0`
- `WEBHOOK_LISTEN_PORT`: webhook 本地监听端口，默认 `8099`
- `WEBHOOK_PATH`: webhook 路径，建议使用随机路径
- `WEBHOOK_SECRET_TOKEN`: Telegram webhook secret token
- `HTTP_PROXY` / `HTTPS_PROXY`: 如果你的服务器访问 Telegram 需要代理，可以配置
- `NO_PROXY`: 本地地址直连，建议包含 `127.0.0.1,localhost` 和 qBittorrent 的局域网地址

## Webhook 模式

使用 Cloudflare Tunnel 时，把 Public Hostname 指向 bot 的本地 webhook 端口：

```text
Hostname: qbit-bot.furyth666.com
Service: http://localhost:8099
```

确认 hostname 可以访问后，把 `.env` 中 `TELEGRAM_MODE` 改为 `webhook`，然后重启容器。

## unRAID 自动部署

当前仓库的自动部署链路是：

1. `git commit`
2. 自动推送 GitHub
3. 自动构建并发布 Docker Hub
4. 自动更新 unRAID 上的 Compose Manager 项目

默认目录：

- Compose 项目: `/boot/config/plugins/compose.manager/projects/qbit-telegram-bot`
- 运行数据: `/mnt/user/appdata/qbit-telegram-bot`

## 自动化规则

- 通过 Telegram 添加的 magnet 任务会自动设置上传限速
- 名称匹配 `JAV_NAME_REGEX` 的任务会在后台自动分类到 `JAV_CATEGORY_NAME`
- 如果 JAV 任务同时包含大文件和小文件，只下载大于 `JAV_LARGE_FILE_THRESHOLD_GB` 的文件
- 如果启用了 Jellyfin 重复检查，首次添加到已存在同番号短片的 JAV magnet 会提醒并删除任务；3 小时内再次添加同番号则保留下载
- 种子下载完成后，bot 会主动发送提醒
- bot 会把“已通知完成”和“已处理过 JAV 分类”的状态写入 `STATE_FILE_PATH`
- 建议挂载 `data/` 目录，避免容器重建后丢失通知和处理状态

## 常见部署说明

如果你的 qBittorrent 容器和这个 bot 在同一个 Docker Compose 网络里，通常可以这样配置：

```env
QBIT_BASE_URL=http://qbittorrent:8080
```

如果 qBittorrent 运行在宿主机，可以改成：

```env
QBIT_BASE_URL=http://host.docker.internal:8080
```

Linux 下如果 `host.docker.internal` 不可用，可以直接填宿主机局域网 IP。
