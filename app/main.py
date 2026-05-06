import logging
import re

from app.bot import create_application
from app.config import Settings


_TELEGRAM_TOKEN_IN_URL = re.compile(r"bot\d{8,12}:AA[A-Za-z0-9_-]+")


class _SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TELEGRAM_TOKEN_IN_URL.sub("bot<redacted>", str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TELEGRAM_TOKEN_IN_URL.sub("bot<redacted>", str(item))
                for item in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: _TELEGRAM_TOKEN_IN_URL.sub("bot<redacted>", str(value))
                for key, value in record.args.items()
            }
        return True


def _configure_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger().addFilter(_SensitiveLogFilter())
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
        bootstrap_retries=-1,
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
