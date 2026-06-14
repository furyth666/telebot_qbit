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
- `/add <一个或多个 magnet/torrent 链接>` 添加下载
- 添加种子后通过按钮选择要移动到的 qBittorrent 分类
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
    image: your-dockerhub-username/qbit-telegram-bot:latest
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
- `QBIT_API_TOKEN`: 可选，qBittorrent Bearer API token。配置后优先使用 token，失败时回退账号密码。
- `BOT_LOG_LEVEL`: 日志等级，默认 `INFO`
- `JAV_CATEGORY_NAME`: `/retryjav` 手动重试时使用的 JAV 分类名称，默认 `JAV`
- `JAV_NAME_REGEX`: JAV 标题匹配规则
- `JAV_LARGE_FILE_THRESHOLD_GB`: JAV 文件筛选阈值，默认 `1`
- `MAGNET_UPLOAD_LIMIT_KIB`: magnet 上传限速，默认 `30`
- `STATE_FILE_PATH`: bot 持久化 SQLite 状态库，默认 `data/bot_state.sqlite3`。旧的 `bot_state.json` 会在首次启动时自动迁移。
- `JELLYFIN_BASE_URL`: Jellyfin API 地址，用于检查是否已有同番号短片
- `JELLYFIN_PUBLIC_BASE_URL`: 返回给 Telegram 的 Jellyfin 内网访问地址
- `JELLYFIN_API_KEY`: Jellyfin API Key
- `JELLYFIN_DUPLICATE_DELETE_ENABLED`: 是否启用 Jellyfin 重复检查
- `JELLYFIN_DUPLICATE_GRACE_HOURS`: 同番号再次添加的保留窗口，默认 `3`
- `LLM_CLASSIFY_ENABLED`: 是否启用大模型自动分类，默认 `false`
- `LLM_API_BASE_URL`: OpenAI-compatible API 地址，默认 `https://api.openai.com/v1`
- `LLM_API_KEY`: 大模型 API Key，启用自动分类时必填
- `LLM_MODEL`: 分类模型，默认 `gpt-4.1-mini`
- `LLM_MIN_CONFIDENCE`: 自动应用分类的最低置信度，默认 `0.85`
- `LLM_REQUEST_TIMEOUT_SECONDS`: 大模型请求超时，默认 `20`
- `LLM_AUTO_APPLY_DELAY_SECONDS`: 发送大模型推荐后等待多久再自动应用分类，默认 `30`
- `WATCHDOG_ENABLED`: 是否启用 bot 自检，默认 `true`
- `WATCHDOG_INTERVAL_SECONDS`: 自检间隔，默认 `300`
- `WATCHDOG_MAX_FAILURES`: 连续失败多少次后退出并交给 Docker 重启，默认 `3`
- `TELEGRAM_CONNECT_TIMEOUT_SECONDS`: Telegram 出站连接超时，默认 `5`
- `TELEGRAM_READ_TIMEOUT_SECONDS`: Telegram 出站读取超时，默认 `8`
- `TELEGRAM_WRITE_TIMEOUT_SECONDS`: Telegram 出站写入超时，默认 `8`
- `TELEGRAM_POOL_TIMEOUT_SECONDS`: Telegram 连接池等待超时，默认 `2`
- `TELEGRAM_CONNECTION_POOL_SIZE`: Telegram HTTP 连接池大小，默认 `8`
- `TELEGRAM_CONCURRENT_UPDATES`: 同时处理的 Telegram update 数量，默认 `4`，避免一个慢 qBittorrent/Jellyfin 请求阻塞所有后续消息。
- `TELEGRAM_NETWORK_ERROR_RESTART_THRESHOLD`: 短时间 Telegram 网络错误达到多少次后请求 Docker 重启，默认 `3`
- `TELEGRAM_NETWORK_ERROR_WINDOW_SECONDS`: Telegram 网络错误统计窗口，默认 `180`
- `WEBHOOK_BASE_URL`: webhook 公网 HTTPS 地址，例如 `https://qbit-bot.example.com`
- `WEBHOOK_LISTEN_HOST`: webhook 本地监听地址，默认 `0.0.0.0`
- `WEBHOOK_LISTEN_PORT`: webhook 本地监听端口，默认 `8099`
- `WEBHOOK_PATH`: webhook 路径，建议使用随机路径
- `WEBHOOK_SECRET_TOKEN`: Telegram webhook secret token
- `WEBHOOK_BOOTSTRAP_RETRIES`: webhook 启动时注册 Telegram webhook 的重试次数，默认 `3`。失败后退出交给 Docker 重启，避免进程存活但端口未监听。
- `HTTP_PROXY` / `HTTPS_PROXY`: 如果你的服务器访问 Telegram 需要代理，可以配置
- `NO_PROXY`: 本地地址直连，建议包含 `127.0.0.1,localhost` 和 qBittorrent 的局域网地址

## Webhook 模式

使用 Cloudflare Tunnel 时，把 Public Hostname 指向 bot 的本地 webhook 端口：

```text
Hostname: qbit-bot.example.com
Service: http://localhost:8099
```

确认 hostname 可以访问后，把 `.env` 中 `TELEGRAM_MODE` 改为 `webhook`，然后重启容器。

Webhook 模式下，如果 qBittorrent 因 unRAID 自动更新短暂返回 `502` 或不可用，bot 会继续保持 webhook 入口在线；qBittorrent 相关命令会在后端恢复后自动恢复。只有 Telegram 自身连续不可达时，watchdog 才会退出并交给 Docker 重启。

## 部署脚本

仓库包含可选部署脚本，实际目标主机、镜像仓库、代理和路径都通过本地 `.deploy/`
配置文件提供。`.deploy/` 已被 `.gitignore` 忽略，不应提交真实部署配置。

## 自动处理

bot 支持添加链接后的后台处理，例如上传限速、分类、文件筛选、重复检查和完成提醒。
具体行为由环境变量控制。
- 启用 `LLM_CLASSIFY_ENABLED` 后，bot 会先发送大模型推荐分类和手动分类按钮。
- 如果已配置 Jellyfin，LLM 分类会从 Jellyfin 媒体名和路径提取已有 JAV 番号前缀作为动态参考。
- 如果 `LLM_AUTO_APPLY_DELAY_SECONDS` 秒内没有手动选择，bot 会自动应用大模型推荐分类。
- 如果你在倒计时内点击分类按钮，自动应用任务会跳过，以手动选择为准。
- HTTP/PT torrent 下载链接会按新增任务 hash 和添加时间窗口定位任务，不使用 `download.php` 等 URL 文件名匹配真实种子标题。
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
