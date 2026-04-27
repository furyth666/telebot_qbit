import logging

from app.bot import create_application
from app.config import Settings


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
    logging.basicConfig(
        level=getattr(logging, settings.bot_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_application(settings)
    if settings.telegram_mode == "webhook":
        _run_webhook(app, settings)
        return

    app.run_polling()


if __name__ == "__main__":
    main()
