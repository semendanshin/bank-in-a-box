"""Мелкие утилиты, общие для API."""
from fastapi import HTTPException


def parse_account_id(value: str) -> int:
    """
    Распарсить идентификатор счёта вида 'acc-123' в целое.

    На некорректном вводе кидает 400 (а не падает 500 на int()).
    """
    if value is None:
        raise HTTPException(400, "Account id is required")
    raw = str(value).replace("acc-", "").strip()
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid account id: {value}")
