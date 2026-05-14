import os
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

_DEFAULT_patcha_DIR = Path.home() / ".patcha"

_PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)
load_dotenv(_DEFAULT_patcha_DIR / ".env", override=True)


class Config(BaseModel):
    openai_api_key: str = ""
    qdrant_url: str = "http://localhost:6333"
    qdrant_path: Path = _DEFAULT_patcha_DIR / "qdrant_storage"
    data_dir: Path = _DEFAULT_patcha_DIR / "data"
    log_level: str = "INFO"
    collection_name: str = "mem"
    vector_size: int = 1536
    max_events_per_day: int = 10000
    max_embedding_tokens: int = 8191
    embedding_chunk_overlap: int = 100
    working_memory_dedup_threshold: float = 0.95
    daily_compaction_min_activities: int = 2
    browser_history_paths: dict = {
        "chrome": "~/Library/Application Support/Google/Chrome/Default/History",
        "arc": "~/Library/Application Support/Arc/User Data/Default/History",
        "firefox": "~/Library/Application Support/Firefox/Profiles/*/places.sqlite",
        "safari": "~/Library/Safari/History.db",
    }
    enable_git_collector: bool = True
    enable_browser_collector: bool = True
    enable_terminal_collector: bool = True
    enable_window_collector: bool = True
    enable_accessibility_collector: bool = True

    # patcha cloud API
    patcha_api_url: str = "https://api.patcha.dev"
    patcha_access_token: str = ""
    patcha_refresh_token: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        def _bool(key: str, default: bool = True) -> bool:
            return os.getenv(key, str(default)).lower() not in ("false", "0", "no")

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            qdrant_path=Path(
                os.getenv("QDRANT_PATH", str(_DEFAULT_patcha_DIR / "qdrant_storage"))
            ),
            data_dir=Path(os.getenv("DATA_DIR", str(_DEFAULT_patcha_DIR / "data"))),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            enable_git_collector=_bool("ENABLE_GIT_COLLECTOR"),
            enable_browser_collector=_bool("ENABLE_BROWSER_COLLECTOR"),
            enable_terminal_collector=_bool("ENABLE_TERMINAL_COLLECTOR"),
            enable_window_collector=_bool("ENABLE_WINDOW_COLLECTOR"),
            enable_accessibility_collector=_bool("ENABLE_ACCESSIBILITY_COLLECTOR"),
            patcha_api_url=os.getenv("PATCHA_API_URL", "https://api.patcha.dev"),
            patcha_access_token=os.getenv("PATCHA_ACCESS_TOKEN", ""),
            patcha_refresh_token=os.getenv("PATCHA_REFRESH_TOKEN", ""),
        )


config = Config.from_env()
