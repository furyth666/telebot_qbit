import logging

from app.bot import create_application
from app.config import Settings


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.bot_log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_application(settings)
    app.run_polling()


if __name__ == "__main__":
    main()
