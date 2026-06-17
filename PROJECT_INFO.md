# Qbit Bot 项目信息

## 这个库是做什么的

这是一个个人使用的 qBittorrent Telegram Bot。它运行在 Docker 里，通过 Telegram 和 qBittorrent Web API 交互，用来远程查看、添加、暂停、恢复、删除下载任务，并在添加任务后做一些自动化处理。

核心能力：

- 通过 Telegram 命令查看 qBittorrent 状态、任务列表和活跃任务。
- 支持添加 magnet 或 torrent 下载链接。
- 添加后可以通过 Telegram 按钮选择 qBittorrent 分类。
- 支持 JAV 相关的自动分类、文件筛选和手动重试。
- 可选接入 Jellyfin，用于检查同番号内容是否已经存在。
- 可选接入 OpenAI-compatible LLM，用于自动推荐分类。
- 支持 polling 和 webhook 两种 Telegram 接收模式。
- 内置 watchdog，Telegram 连续网络错误时可退出并交给 Docker 自动重启。
- 启动时会恢复最近 24 小时内被部署/重启打断的 JAV 后处理任务。

主要入口：

- 应用入口：`python -m app.main`
- Docker 入口：`Dockerfile`
- 本地 Compose：`docker-compose.yml`
- 部署脚本：`scripts/sync_unraid.sh`

项目定位：

- 这是个人工具，不要把它抽象成通用框架。
- 重构时优先保持当前行为稳定，尤其是分类按钮流程、qBittorrent 认证回退、Telegram 网络恢复逻辑和 unRAID 部署路径。
- 外部服务适配保持明确：qBittorrent、Jellyfin、LLM、Telegram 都是独立边界。
- 不要恢复“收到链接后立即先回复正在提交”的交互；用户明确不要这个。发送 magnet/下载链接后，应等添加完成再回复结果。
- HTTP/PT torrent 链接不要再用 `download.php` 等 URL basename 当真实种子标题匹配依据。

## 我的基础信息

- 本地仓库路径：`/Users/furyth666/Code/Qbit_bot`
- 主要部署目标：个人 unRAID 主机
- 提到 unRAID 时，可以直接使用 `ssh unraid` 连接。
- unRAID 本机有 Clash 代理，代理地址是 `192.168.50.46:7897`。
- 线上 webhook 域名曾验证为 `https://qbit-bot.furyth666.com/`，Cloudflare/Tornado 未命中路径返回 `404` 属于正常探测结果。
- qBittorrent 线上分类曾验证包含：`AV`、`JAV`、`做种`、`写真`、`电影`。实际部署时仍以 qBittorrent 当前分类 API 返回为准。
- 部署配置放在本地 `.deploy/` 目录中，该目录不应提交到 Git。
- 运行时敏感配置放在 `.env` 或 unRAID 端的 Compose env 文件中，不应提交真实 token、密码或 API key。

## 关键配置文件

- `.env.example`：应用运行环境变量模板。
- `.deploy.example.env`：unRAID 部署脚本配置模板。
- `.deploy.dockerhub.example.env`：Docker Hub 发布配置模板。
- `docker-compose.yml`：本地或直接部署时使用的 Compose 配置。
- `scripts/README.md`：部署脚本的简要说明。

重要环境变量：

- `TELEGRAM_BOT_TOKEN`：Telegram Bot token。
- `TELEGRAM_ALLOWED_USER_IDS`：允许访问 bot 的 Telegram 用户 ID。
- `TELEGRAM_MODE`：`polling` 或 `webhook`。
- `QBIT_BASE_URL`：qBittorrent WebUI 地址。
- `QBIT_USERNAME` / `QBIT_PASSWORD`：qBittorrent 登录账号密码。
- `STATE_FILE_PATH`：SQLite 状态库路径，默认 `data/bot_state.sqlite3`。
- `JELLYFIN_BASE_URL` / `JELLYFIN_API_KEY`：Jellyfin 集成配置。
- `LLM_CLASSIFY_ENABLED` / `LLM_API_BASE_URL` / `LLM_API_KEY`：LLM 自动分类配置。
- `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`：访问 Telegram 或外部 API 需要代理时使用。

## 代码架构速记

启动流程：

- `app.main` 创建 `Settings`、配置日志，然后调用 `app.bot.create_application` 构建 Telegram `Application`。
- `create_application` 注册所有 handlers，并把 `Settings`、`QbitClient`、`JellyfinClient` 注入 `Application.bot_data`。
- `app.runtime_state.RuntimeContext` 是 `bot_data` 的 typed wrapper。跨 handler 的运行时状态都应该通过它访问，不要新增散落的全局变量。
- `app.lifecycle.post_init` 会加载 SQLite 状态、设置 Telegram commands、初始化 qBittorrent 完成任务 baseline，并启动 completion monitor 和 watchdog。
- `app.lifecycle.post_shutdown` 负责取消后台任务、关闭客户端、保存状态。

添加下载流程：

- `app.add_links` 从文本中提取 magnet/HTTP 链接，提交到 qBittorrent，并返回 `AddBatchResult`。magnet 会自动设置上传限速。
- magnet context 会保留 `dn` 作为 `name_hint`；HTTP/PT torrent URL 的 `name_hint` 保持 `None`，避免 `download.php` 误匹配。
- `app.add_flow` 负责安排后台任务，轮询 qBittorrent 来定位新增任务。
- `app.jobs.background_finalize_torrent` 定位到任务后，优先尝试 LLM 分类；失败、未启用或置信度不足时，回退到 Telegram 分类按钮。
- 如果命中 JAV 规则，`app.jav_policy` 会处理 JAV 分类、文件筛选、Jellyfin 重复检查和 processed 状态持久化。

Callback 与分类：

- Telegram callback 使用紧凑格式：`tor:<action>:<view>:<payload>`。
- callback 构建和解析集中在 `app.callback_data`。
- 分类选择 payload 是 `<hash>:<index>`。
- `app.category_flow` 管理分类 prompt、pending choices、LLM 自动应用倒计时和手动分类选择。

JAV 和 Jellyfin 策略：

- `app.jav_policy` 是 JAV 后处理的单一入口。
- 会创建/应用 JAV 分类，并按阈值筛选 torrent 文件优先级。
- Jellyfin 重复检查结果包括 `NONE`、`WITHIN_GRACE`、`FOUND_KEEP`、`FOUR_K_EXCEPTION`、`DELETED`。
- 如果 Jellyfin 已有同番号且启用自动删除，magnet 任务可被自动删除，并记录 3 小时 grace window。
- 4K 版本有例外：Jellyfin 中没有同 4K 版本时保留下载。
- 启动恢复逻辑只处理最近 24 小时内、已经在 `JAV` 分类、标题能识别番号、但状态库没有 `jav_processed` 标记的任务。
- 这个恢复逻辑用于修复部署/重启发生在“设置 JAV 分类”和“小文件过滤、写状态、发通知”之间时留下的半处理任务。

LLM 分类：

- `app.llm_classifier.classify_torrent` 调用 OpenAI-compatible API。
- 如果 `LLM_API_BASE_URL` 指向本地 Ollama，会使用 Ollama 原生 `/api/chat`。
- LLM 请求使用 `trust_env=False`，避免代理影响本地或局域网 LLM 服务。
- Prompt 会注入 JAV 分类策略，以及从 Jellyfin 动态提取的已有 JAV 番号前缀。
- 当前产品决策是“先正则识别 JAV，再交给 LLM 分类其他内容”：只要正则命中番号，就直接走 JAV 分类、查重、文件筛选，不让大模型猜。
- LLM 高置信分类不会立刻应用，会先发推荐和手动分类按钮。
- 默认 `LLM_AUTO_APPLY_DELAY_SECONDS=30`。倒计时内无人手动选择时，才自动应用 LLM 推荐分类。
- 如果用户在倒计时内点击分类按钮，自动应用任务会跳过。
- LLM 低置信、非法分类或调用失败时，继续走手动分类按钮。

状态持久化：

- `app.state_store.StateStore` 使用 SQLite WAL 模式。
- 旧的 `bot_state.json` 会在首次启动时自动迁移。
- 持久化内容包括 completed 通知 hash、JAV processed hash、Jellyfin duplicate grace window。
- `notified_completed_hashes` 默认 90 天 TTL。
- `jav_processed_hashes` 默认 180 天 TTL。

外部客户端约定：

- qBittorrent 客户端优先使用 `QBIT_API_TOKEN` Bearer token，失败后回退账号密码。
- pause/resume 会兼容新旧 qBittorrent API endpoint。
- qBittorrent、Jellyfin、本地 Ollama 相关请求使用 `trust_env=False`，避免局域网请求被代理劫持。

Watchdog 行为：

- Telegram 连续失败达到阈值时，bot 会退出并交给 Docker 重启。
- qBittorrent 检查失败只记日志，bot 继续运行，避免 webhook 入口因为后端短暂不可用而离线。
- Telegram 网络错误还有独立短期窗口统计，默认 180 秒内 3 次触发重启。

## 本地运行方式

首次准备：

```bash
cp .env.example .env
```

然后编辑 `.env`，至少填好：

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=...
QBIT_BASE_URL=...
QBIT_USERNAME=...
QBIT_PASSWORD=...
```

启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker logs -f qbit-telegram-bot
```

停止：

```bash
docker compose down
```

运行验证：

```bash
bash scripts/validate.sh
```

开发验证命令：

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_add_flow -v
python -m py_compile app/*.py tests/*.py
git diff --check
```

当前维护基线：

- 全量 unittest 应通过。
- `python -m py_compile app/*.py tests/*.py` 应通过。
- `git diff --check` 应通过。
- `bash scripts/validate.sh` 是提交或部署前的统一验证入口。
- 本机 Docker daemon 不运行时，提交 hook 里的 Docker Hub 发布可能失败；这不等同于 GitHub push 或 unRAID 部署失败，需要分别看实际结果。

## unRAID 部署方式

推荐使用仓库内脚本同步到 unRAID，并由脚本在 unRAID 上生成 Compose 配置、build 镜像和重启容器。

### 1. 准备部署配置

```bash
mkdir -p .deploy
cp .deploy.example.env .deploy/unraid.env
```

编辑 `.deploy/unraid.env`。常用配置：

```env
UNRAID_HOST=unraid.local
UNRAID_PORT=22
UNRAID_USER=your_unraid_user
UNRAID_APPDATA_DIR=/mnt/user/appdata/qbit-telegram-bot
UNRAID_COMPOSE_PROJECT_DIR=/boot/config/plugins/compose.manager/projects/qbit-telegram-bot
UNRAID_SSH_KEY=~/.ssh/qbit_unraid_ed25519
UNRAID_HOST_NETWORK_ACK=I_UNDERSTAND_HOST_NETWORK_IS_INTENTIONAL
```

如果 Telegram、OpenAI-compatible API 或其他外部服务需要走 unRAID 本机 Clash，可在 `.deploy/unraid.env` 中配置：

```env
UNRAID_HTTP_PROXY=http://192.168.50.46:7897
UNRAID_HTTPS_PROXY=http://192.168.50.46:7897
UNRAID_NO_PROXY=localhost,127.0.0.1,192.168.50.0/24
```

### 2. 首次配置 SSH 免密

```bash
bash scripts/setup_unraid_key.sh '你的unraid密码'
```

之后也可以直接测试：

```bash
ssh unraid
```

### 3. 准备 unRAID 端运行环境变量

脚本默认会使用：

```text
/boot/config/plugins/compose.manager/projects/qbit-telegram-bot/.env
```

作为 Compose env 文件。

如果旧位置存在：

```text
/mnt/user/appdata/qbit-telegram-bot/.env
```

脚本会把它移动到 Compose 项目目录，并改名保留旧文件。

### 4. 同步并部署

```bash
bash scripts/sync_unraid.sh
```

脚本会做这些事：

- 本地执行 `python3 -m py_compile app/*.py`。
- 通过 rsync 同步仓库到 `/mnt/user/appdata/qbit-telegram-bot`。
- 保护远端 `.env` 和 `data/`，避免同步时覆盖运行配置和状态库。
- 在 unRAID Compose Manager 项目目录生成 Compose 文件。
- 删除旧容器并执行 `docker compose up -d --build`。
- 输出 `qbit-telegram-bot` 容器状态。

### 5. unRAID Compose 路径约定

默认应用数据目录：

```text
/mnt/user/appdata/qbit-telegram-bot
```

默认 Compose Manager 项目目录：

```text
/boot/config/plugins/compose.manager/projects/qbit-telegram-bot
```

持久化状态目录：

```text
/mnt/user/appdata/qbit-telegram-bot/data
```

容器内挂载：

```text
/app/data
```

### 6. 部署后验收

行为变更或部署相关改动后，建议在 unRAID 上检查：

```bash
ssh unraid "docker ps --filter name=qbit-telegram-bot"
ssh unraid "docker logs --tail 100 qbit-telegram-bot"
```

验收点：

- 容器状态 healthy 或至少持续运行。
- webhook 模式下，`8099` 端口正在监听；旧验证中看到过 `0.0.0.0:8099`。
- Cloudflare Tunnel 公网探测能到达 bot，普通未命中路径返回预期 `404`，例如 `https://qbit-bot.furyth666.com/`。
- Telegram webhook info 返回 `ok=True`。
- `pending_update_count=0`。
- qBittorrent 短暂不可用时，bot 不应退出；Telegram 连续不可达时才应由 watchdog 触发重启。
- qBittorrent 分类 API 能读到线上分类；旧验证分类包括 `AV,JAV,做种,写真,电影`。

## 网络和安全说明

unRAID 部署当前使用 `network_mode: host`。这是刻意选择，原因是 bot 需要稳定访问宿主机或局域网中的 qBittorrent WebUI、Cloudflare Tunnel 和代理服务。

host 网络只建议用于受信任的个人 unRAID 主机。脚本生成的 Compose 会同时使用这些限制降低风险：

- `security_opt: no-new-privileges:true`
- `cap_drop: ALL`
- `read_only: true`
- 只挂载 `/app/data` 作为持久化写入目录
- 使用 `/tmp` tmpfs

如果迁移到普通 Linux 服务器或多租户环境，优先改成 bridge 网络，并把 `QBIT_BASE_URL` 指向同一个 Compose 网络里的 qBittorrent 服务名，或明确指向局域网地址。

## Webhook 模式

如果使用 Cloudflare Tunnel，把 Public Hostname 指向 bot 的本地 webhook 端口：

```text
Service: http://localhost:8099
```

`.env` 中需要设置：

```env
TELEGRAM_MODE=webhook
WEBHOOK_BASE_URL=https://你的域名
WEBHOOK_LISTEN_HOST=127.0.0.1
WEBHOOK_LISTEN_PORT=8099
WEBHOOK_PATH=telegram/随机路径
WEBHOOK_SECRET_TOKEN=至少16位随机字符串
```

如果只是个人使用并且不想配置公网 HTTPS 入口，可以继续使用默认的 `polling` 模式。

## 常用维护命令

本地验证：

```bash
bash scripts/validate.sh
```

部署到 unRAID：

```bash
bash scripts/sync_unraid.sh
```

发布 Docker Hub：

```bash
bash scripts/publish_dockerhub.sh
```

查看 unRAID 容器状态：

```bash
ssh unraid "docker ps --filter name=qbit-telegram-bot"
```

查看 unRAID 容器日志：

```bash
ssh unraid "docker logs -f qbit-telegram-bot"
```

重启 unRAID 容器：

```bash
ssh unraid "docker restart qbit-telegram-bot"
```

检查 webhook 监听端口：

```bash
ssh unraid "ss -lntp | grep 8099"
```

查看 Compose 项目文件：

```bash
ssh unraid "ls -la /boot/config/plugins/compose.manager/projects/qbit-telegram-bot"
```

## 不要提交的内容

- `.env`
- `.deploy/`
- `data/`
- Telegram Bot token
- qBittorrent 密码或 API token
- Jellyfin API key
- LLM API key
- unRAID SSH 私钥
