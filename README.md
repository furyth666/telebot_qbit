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

## 使用方式

1. 创建 Telegram Bot，拿到 `TELEGRAM_BOT_TOKEN`
2. 获取你自己的 Telegram 用户 ID，并填入 `TELEGRAM_ALLOWED_USER_IDS`
3. 在 qBittorrent 中开启 WebUI，并确认账号密码可用
4. 复制环境变量模板：

```bash
cp .env.example .env
```

5. 修改 `.env`
6. 启动：

```bash
docker compose up -d --build
```

## 环境变量

- `TELEGRAM_BOT_TOKEN`: Telegram 机器人 token
- `TELEGRAM_ALLOWED_USER_IDS`: 允许访问的 Telegram 用户 ID，多个用逗号分隔
- `QBIT_BASE_URL`: qBittorrent WebUI 地址
- `QBIT_USERNAME`: qBittorrent 用户名
- `QBIT_PASSWORD`: qBittorrent 密码
- `BOT_LOG_LEVEL`: 日志等级，默认 `INFO`
- `HTTP_PROXY` / `HTTPS_PROXY`: 如果你的服务器访问 Telegram 需要代理，可以配置
- `NO_PROXY`: 本地地址直连，建议包含 `127.0.0.1,localhost` 和 qBittorrent 的局域网地址

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
