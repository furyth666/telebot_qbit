import os
from dataclasses import dataclass


def _split_user_ids(raw: str) -> list[int]:
    user_ids: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        user_ids.append(int(item))
    return user_ids


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_ids: list[int]
    qbit_base_url: str
    qbit_username: str
    qbit_password: str
    bot_log_level: str = "INFO"
    jav_category_name: str = "JAV"
    jav_name_regex: str = r"[A-Za-z]{2,}-\d{2,}"
    jav_large_file_threshold_gb: float = 1.0
    magnet_upload_limit_kib: int = 30
    state_file_path: str = "data/bot_state.json"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            telegram_allowed_user_ids=_split_user_ids(
                os.environ["TELEGRAM_ALLOWED_USER_IDS"]
            ),
            qbit_base_url=os.environ["QBIT_BASE_URL"].rstrip("/"),
            qbit_username=os.environ["QBIT_USERNAME"],
            qbit_password=os.environ["QBIT_PASSWORD"],
            bot_log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            jav_category_name=os.getenv("JAV_CATEGORY_NAME", "JAV"),
            jav_name_regex=os.getenv("JAV_NAME_REGEX", r"[A-Za-z]{2,}-\d{2,}"),
            jav_large_file_threshold_gb=float(
                os.getenv("JAV_LARGE_FILE_THRESHOLD_GB", "1")
            ),
            magnet_upload_limit_kib=int(os.getenv("MAGNET_UPLOAD_LIMIT_KIB", "30")),
            state_file_path=os.getenv("STATE_FILE_PATH", "data/bot_state.json"),
        )
