"""Логи з id запиту. Налаштування передається в uvicorn (працює і з --workers N)."""

import logging
from contextvars import ContextVar
from typing import Any

# id поточного запиту. ContextVar, а не глобальна змінна: кожен запит (корутина
# або потік з пулу) бачить своє значення
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def log_config(level: str = "INFO", access_log: bool = True) -> dict[str, Any]:
    """dictConfig для uvicorn: логи в stdout (платформа їх показує в панелі)."""
    fmt = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": "findex.web.logs.RequestIdFilter"}},
        "formatters": {"default": {"format": fmt}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "default",
                "filters": ["request_id"],
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.access": {
                "handlers": [],
                "propagate": True,
                "level": "INFO" if access_log else "WARNING",
            },
        },
    }
