"""Конфіг тільки з оточення (12-factor, правило III): той самий образ - і локально, і на сервері."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Читається зі змінних оточення (і з .env під час розробки).

    INDEX_PATH=index_pos.bin LOG_LEVEL=INFO findex serve
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    index_path: Path = Path("index_pos.bin")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Тільки для бенчмарку (Lab 7, M2/M4): "async" реєструє /search як async def,
    # щоб поміряти, як це блокує цикл подій. Типово - звичайний def.
    search_mode: Literal["def", "async"] = "def"


@lru_cache
def get_settings() -> Settings:
    # один об'єкт на процес; у тестах підміняється через dependency_overrides
    return Settings()
