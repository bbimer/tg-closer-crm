import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Base project directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    # Telegram MTProto Credentials
    telegram_api_id: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")

    # Directories and Paths
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    exports_dir: Path = BASE_DIR / "exports"
    telegram_sessions_dir: Path = Path(
        os.getenv("TELEGRAM_SESSIONS_DIR", str(BASE_DIR / "sessions"))
    )
    raw_crm_path = os.getenv("CRM_DB_PATH", "data/crm.db")
    crm_db_path: Path = (
        (BASE_DIR / raw_crm_path).resolve()
        if not Path(raw_crm_path).is_absolute()
        else Path(raw_crm_path)
    )

    # LLM Settings
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek/deepseek-chat")

    # Throttling & Anti-Flood
    fetch_delay_min: float = float(os.getenv("FETCH_DELAY_MIN", "1.5"))
    fetch_delay_max: float = float(os.getenv("FETCH_DELAY_MAX", "3.5"))
    flood_wait_backoff_factor: float = float(
        os.getenv("FLOOD_WAIT_BACKOFF_FACTOR", "1.5")
    )
    batch_size: int = int(os.getenv("BATCH_SIZE", "100"))


# Singleton settings instance
settings = Settings()

# Ensure runtime directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.exports_dir.mkdir(parents=True, exist_ok=True)
