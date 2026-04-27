import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()


class Config(BaseModel):
    openai_api_key: str
    qdrant_url: str = "http://localhost:6333"
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    collection_name: str = "mem"
    vector_size: int = 1536
    max_events_per_day: int = 10000
    browser_history_paths: dict = {
        "chrome": "~/Library/Application Support/Google/Chrome/Default/History",
        "arc": "~/Library/Application Support/Arc/User Data/Default/History",
        "firefox": "~/Library/Application Support/Firefox/Profiles/*/places.sqlite",
        "safari": "~/Library/Safari/History.db"
    }

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            log_level=os.getenv("LOG_LEVEL", "INFO")
        )


config = Config.from_env()
