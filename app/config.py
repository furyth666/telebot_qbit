import os
import re
from dataclasses import dataclass

from app.jav_patterns import DEFAULT_JAV_NAME_REGEX


def _split_user_ids(raw: str) -> list[int]:
    user_ids: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            user_ids.append(int(item))
        except ValueError as exc:
            raise ValueError(f"TELEGRAM_ALLOWED_USER_IDS 包含无效用户 ID: {item}") from exc
    return user_ids


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"缺少必要环境变量: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_ids: list[int]
    qbit_base_url: str
    qbit_username: str
    qbit_password: str
    qbit_api_token: str = ""
    telegram_mode: str = "polling"
    bot_log_level: str = "INFO"
    jav_category_name: str = "JAV"
    jav_name_regex: str = DEFAULT_JAV_NAME_REGEX
    jav_large_file_threshold_gb: float = 1.0
    magnet_upload_limit_kib: int = 30
    state_file_path: str = "data/bot_state.sqlite3"
    jellyfin_base_url: str = ""
    jellyfin_public_base_url: str = ""
    jellyfin_api_key: str = ""
    jellyfin_duplicate_delete_enabled: bool = False
    jellyfin_duplicate_grace_hours: int = 3
    llm_classify_enabled: bool = False
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    llm_min_confidence: float = 0.85
    llm_request_timeout_seconds: float = 20.0
    watchdog_enabled: bool = True
    watchdog_interval_seconds: int = 300
    watchdog_max_failures: int = 3
    telegram_connect_timeout_seconds: float = 5.0
    telegram_read_timeout_seconds: float = 8.0
    telegram_write_timeout_seconds: float = 8.0
    telegram_pool_timeout_seconds: float = 2.0
    telegram_connection_pool_size: int = 8
    telegram_concurrent_updates: int = 4
    telegram_network_error_restart_threshold: int = 3
    telegram_network_error_window_seconds: int = 180
    webhook_base_url: str = ""
    webhook_listen_host: str = "0.0.0.0"
    webhook_listen_port: int = 8099
    webhook_path: str = ""
    webhook_secret_token: str = ""
    webhook_bootstrap_retries: int = 3

    def validate(self) -> "Settings":
        errors: list[str] = []

        if not self.telegram_bot_token.strip():
            errors.append("TELEGRAM_BOT_TOKEN 不能为空")
        if not self.telegram_allowed_user_ids:
            errors.append("TELEGRAM_ALLOWED_USER_IDS 至少需要配置一个用户 ID")
        if not self.qbit_base_url:
            errors.append("QBIT_BASE_URL 不能为空")
        if not self.qbit_api_token:
            if not self.qbit_username:
                errors.append("QBIT_USERNAME 不能为空")
            if not self.qbit_password:
                errors.append("QBIT_PASSWORD 不能为空")
        if self.telegram_mode not in {"polling", "webhook"}:
            errors.append("TELEGRAM_MODE 只能是 polling 或 webhook")
        try:
            re.compile(self.jav_name_regex)
        except re.error as exc:
            errors.append(f"JAV_NAME_REGEX 不是有效正则: {exc}")
        if self.jav_large_file_threshold_gb <= 0:
            errors.append("JAV_LARGE_FILE_THRESHOLD_GB 必须大于 0")
        if self.magnet_upload_limit_kib < 0:
            errors.append("MAGNET_UPLOAD_LIMIT_KIB 不能小于 0")
        if self.jellyfin_duplicate_grace_hours <= 0:
            errors.append("JELLYFIN_DUPLICATE_GRACE_HOURS 必须大于 0")
        if self.llm_classify_enabled and not self.llm_api_key.strip():
            errors.append("LLM_CLASSIFY_ENABLED=true 时必须配置 LLM_API_KEY")
        if not self.llm_api_base_url:
            errors.append("LLM_API_BASE_URL 不能为空")
        if not self.llm_model:
            errors.append("LLM_MODEL 不能为空")
        if self.llm_min_confidence < 0 or self.llm_min_confidence > 1:
            errors.append("LLM_MIN_CONFIDENCE 必须在 0 到 1 之间")
        if self.llm_request_timeout_seconds <= 0:
            errors.append("LLM_REQUEST_TIMEOUT_SECONDS 必须大于 0")
        if self.watchdog_interval_seconds <= 0:
            errors.append("WATCHDOG_INTERVAL_SECONDS 必须大于 0")
        if self.watchdog_max_failures <= 0:
            errors.append("WATCHDOG_MAX_FAILURES 必须大于 0")
        if self.telegram_connect_timeout_seconds <= 0:
            errors.append("TELEGRAM_CONNECT_TIMEOUT_SECONDS 必须大于 0")
        if self.telegram_read_timeout_seconds <= 0:
            errors.append("TELEGRAM_READ_TIMEOUT_SECONDS 必须大于 0")
        if self.telegram_write_timeout_seconds <= 0:
            errors.append("TELEGRAM_WRITE_TIMEOUT_SECONDS 必须大于 0")
        if self.telegram_pool_timeout_seconds <= 0:
            errors.append("TELEGRAM_POOL_TIMEOUT_SECONDS 必须大于 0")
        if self.telegram_connection_pool_size <= 0:
            errors.append("TELEGRAM_CONNECTION_POOL_SIZE 必须大于 0")
        if self.telegram_concurrent_updates <= 0:
            errors.append("TELEGRAM_CONCURRENT_UPDATES 必须大于 0")
        if self.telegram_network_error_restart_threshold <= 0:
            errors.append("TELEGRAM_NETWORK_ERROR_RESTART_THRESHOLD 必须大于 0")
        if self.telegram_network_error_window_seconds <= 0:
            errors.append("TELEGRAM_NETWORK_ERROR_WINDOW_SECONDS 必须大于 0")
        if self.telegram_mode == "webhook":
            if not self.webhook_base_url:
                errors.append("Webhook 模式需要配置 WEBHOOK_BASE_URL")
            if not self.webhook_path:
                errors.append("Webhook 模式需要配置 WEBHOOK_PATH")
            if self.webhook_listen_port <= 0 or self.webhook_listen_port > 65535:
                errors.append("WEBHOOK_LISTEN_PORT 必须在 1-65535 之间")
            if self.webhook_bootstrap_retries < 0:
                errors.append("WEBHOOK_BOOTSTRAP_RETRIES 不能小于 0")

        if errors:
            raise ValueError("配置验证失败:\n- " + "\n- ".join(errors))
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_mode=os.getenv("TELEGRAM_MODE", "polling").strip().lower(),
            telegram_bot_token=_required_env("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_user_ids=_split_user_ids(
                _required_env("TELEGRAM_ALLOWED_USER_IDS")
            ),
            qbit_base_url=_required_env("QBIT_BASE_URL").rstrip("/"),
            qbit_username=os.getenv("QBIT_USERNAME", ""),
            qbit_password=os.getenv("QBIT_PASSWORD", ""),
            qbit_api_token=os.getenv("QBIT_API_TOKEN", ""),
            bot_log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            jav_category_name=os.getenv("JAV_CATEGORY_NAME", "JAV"),
            jav_name_regex=os.getenv("JAV_NAME_REGEX", DEFAULT_JAV_NAME_REGEX),
            jav_large_file_threshold_gb=float(
                os.getenv("JAV_LARGE_FILE_THRESHOLD_GB", "1")
            ),
            magnet_upload_limit_kib=int(os.getenv("MAGNET_UPLOAD_LIMIT_KIB", "30")),
            state_file_path=os.getenv("STATE_FILE_PATH", "data/bot_state.sqlite3"),
            jellyfin_base_url=os.getenv("JELLYFIN_BASE_URL", "").rstrip("/"),
            jellyfin_public_base_url=os.getenv("JELLYFIN_PUBLIC_BASE_URL", "").rstrip("/"),
            jellyfin_api_key=os.getenv("JELLYFIN_API_KEY", ""),
            jellyfin_duplicate_delete_enabled=_as_bool(
                os.getenv("JELLYFIN_DUPLICATE_DELETE_ENABLED"),
                False,
            ),
            jellyfin_duplicate_grace_hours=int(
                os.getenv("JELLYFIN_DUPLICATE_GRACE_HOURS", "3")
            ),
            llm_classify_enabled=_as_bool(os.getenv("LLM_CLASSIFY_ENABLED"), False),
            llm_api_base_url=os.getenv(
                "LLM_API_BASE_URL",
                "https://api.openai.com/v1",
            ).rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            llm_min_confidence=float(os.getenv("LLM_MIN_CONFIDENCE", "0.85")),
            llm_request_timeout_seconds=float(
                os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "20")
            ),
            watchdog_enabled=_as_bool(os.getenv("WATCHDOG_ENABLED"), True),
            watchdog_interval_seconds=int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "300")),
            watchdog_max_failures=int(os.getenv("WATCHDOG_MAX_FAILURES", "3")),
            telegram_connect_timeout_seconds=float(
                os.getenv("TELEGRAM_CONNECT_TIMEOUT_SECONDS", "5")
            ),
            telegram_read_timeout_seconds=float(
                os.getenv("TELEGRAM_READ_TIMEOUT_SECONDS", "8")
            ),
            telegram_write_timeout_seconds=float(
                os.getenv("TELEGRAM_WRITE_TIMEOUT_SECONDS", "8")
            ),
            telegram_pool_timeout_seconds=float(
                os.getenv("TELEGRAM_POOL_TIMEOUT_SECONDS", "2")
            ),
            telegram_connection_pool_size=int(
                os.getenv("TELEGRAM_CONNECTION_POOL_SIZE", "8")
            ),
            telegram_concurrent_updates=int(
                os.getenv("TELEGRAM_CONCURRENT_UPDATES", "4")
            ),
            telegram_network_error_restart_threshold=int(
                os.getenv("TELEGRAM_NETWORK_ERROR_RESTART_THRESHOLD", "3")
            ),
            telegram_network_error_window_seconds=int(
                os.getenv("TELEGRAM_NETWORK_ERROR_WINDOW_SECONDS", "180")
            ),
            webhook_base_url=os.getenv("WEBHOOK_BASE_URL", "").rstrip("/"),
            webhook_listen_host=os.getenv("WEBHOOK_LISTEN_HOST", "0.0.0.0"),
            webhook_listen_port=int(os.getenv("WEBHOOK_LISTEN_PORT", "8099")),
            webhook_path=os.getenv("WEBHOOK_PATH", "").strip("/"),
            webhook_secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", ""),
            webhook_bootstrap_retries=int(
                os.getenv("WEBHOOK_BOOTSTRAP_RETRIES", "3")
            ),
        ).validate()
