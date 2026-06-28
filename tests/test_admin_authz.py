"""Тесты защиты /admin (раньше отдавал client_secret команд без авторизации)."""
from _helpers import client_token, banker_token, auth


async def test_admin_teams_requires_auth(client, session_maker):
    r = await client.get("/admin/teams")
    assert r.status_code in (401, 403)


async def test_admin_rejects_client_token(client, session_maker):
    r = await client.get("/admin/teams", headers=auth(client_token("team218-1")))
    assert r.status_code in (401, 403)


async def test_admin_allows_banker(client, session_maker):
    r = await client.get("/admin/capital", headers=auth(banker_token()))
    assert r.status_code == 200, r.text


async def test_banker_requires_auth(client, session_maker):
    r = await client.get("/banker/clients")
    assert r.status_code in (401, 403)


async def test_banker_rejects_client_token(client, session_maker):
    r = await client.get("/banker/products", headers=auth(client_token("team218-1")))
    assert r.status_code in (401, 403)


async def test_banker_allows_banker(client, session_maker):
    r = await client.get("/banker/products", headers=auth(banker_token()))
    assert r.status_code == 200, r.text
