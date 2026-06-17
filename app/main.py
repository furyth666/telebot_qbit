import logging
import re

from app.bot import create_application
from app.config import Settings


_TELEGRAM_TOKEN_IN_URL = re.compile(r"bot\d{8,12}:AA[A-Za-z0-9_-]+")
_BEARER_TOKEN = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?(?:bearer|apikey)\s+)[^'\"\s,)}]+")
_EMBY_TOKEN = re.compile(r"(?i)(x-emby-token['\"]?\s*[:=]\s*['\"]?)[^'\"\s,)}]+")
_PASSWORD_FIELD = re.compile(r"(?i)(password['\"]?\s*[:=]\s*['\"]?)[^'\"\s,)}]+")


class _SensitiveFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        message = _TELEGRAM_TOKEN_IN_URL.sub("bot<redacted>", message)
        message = _BEARER_TOKEN.sub(r"\1<redacted>", message)
        message = _EMBY_TOKEN.sub(r"\1<redacted>", message)
        message = _PASSWORD_FIELD.sub(r"\1<redacted>", message)
        return message


def _configure_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        handlers=[
            logging.StreamHandler(),
        ],
    )
    formatter = _SensitiveFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setFormatter(formatter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _run_webhook(app, settings: Settings) -> None:
    if not settings.webhook_base_url or not settings.webhook_path:
        raise RuntimeError(
            "Webhook 模式需要配置 WEBHOOK_BASE_URL 和 WEBHOOK_PATH。"
        )

    webhook_url = f"{settings.webhook_base_url}/{settings.webhook_path}"
    app.run_webhook(
        listen=settings.webhook_listen_host,
        port=settings.webhook_listen_port,
        url_path=settings.webhook_path,
        webhook_url=webhook_url,
        secret_token=settings.webhook_secret_token or None,
        bootstrap_retries=settings.webhook_bootstrap_retries,
    )


def main() -> None:
    settings = Settings.from_env()
    _configure_logging(getattr(logging, settings.bot_log_level.upper(), logging.INFO))
    app = create_application(settings)
    if settings.telegram_mode == "webhook":
        _run_webhook(app, settings)
        return

    app.run_polling()


if __name__ == "__main__":
    main()
