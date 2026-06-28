"""
Smoke-тест: пройтись по ВСЕМ GET-эндпоинтам на засиженной БД и убедиться,
что ни один не отдаёт 500. 4xx (валидация/авторизация/не найдено) допустимы —
ловим именно серверные ошибки (NameError/AttributeError/неверные поля и т.п.).
"""
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

import main
import models
import seed
from seed import seed_if_empty
from _helpers import client_token, banker_token, team_token, auth


def _fill_path(path: str, account_id: int, card_id: str) -> str:
    repl = {
        "account_id": f"acc-{account_id}",
        "card_id": card_id,
        "bank_code": "vbank",
        "client_id": f"{seed.TEAM_ID}-1",
    }
    for name, value in repl.items():
        path = path.replace("{" + name + "}", str(value))
    # Остальные path-параметры -> заведомо отсутствующий id (ждём 404, не 500)
    import re
    path = re.sub(r"\{[^}]+\}", "missing-id", path)
    return path


async def test_no_get_endpoint_returns_500(client, session_maker):
    async with session_maker() as s:
        await seed_if_empty(s)
        acc = (await s.execute(
            select(models.Account).join(models.Client)
            .where(models.Client.person_id == f"{seed.TEAM_ID}-1")
        )).scalars().first()
        card = (await s.execute(select(models.Card))).scalars().first()
        account_id = acc.id
        card_id = card.card_id if card else "missing-id"

    tokens = {
        "client": auth(client_token(f"{seed.TEAM_ID}-1")),
        "team": auth(team_token(seed.TEAM_ID)),
        "banker": auth(banker_token()),
    }

    # Отдельный клиент, который НЕ пробрасывает исключения приложения,
    # чтобы 5xx превратился в ответ и мы собрали ВСЕ проблемы разом.
    from httpx import AsyncClient, ASGITransport
    transport = ASGITransport(app=main.app, raise_app_exceptions=False)

    failures = []
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for route in main.app.routes:
            if not isinstance(route, APIRoute):
                continue
            if "GET" not in route.methods:
                continue
            path = _fill_path(route.path, account_id, card_id)
            for label, headers in tokens.items():
                resp = await c.get(path, headers=headers)
                if resp.status_code >= 500:
                    failures.append(f"{route.path} [{label}] -> {resp.status_code}: {resp.text[:200]}")

    assert not failures, "5xx на эндпоинтах:\n" + "\n".join(failures)
