"""
Тесты авторизации доступа к счетам (закрытие IDOR):
чтение баланса/транзакций/деталей и смена статуса — только владельцем
или межбанком по согласию.
"""
from _helpers import add_account, account_id, client_token, team_token, auth


async def test_owner_reads_own_balance(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.get(f"/accounts/acc-{acc}/balances", headers=auth(client_token("team218-1")))
    assert r.status_code == 200, r.text


async def test_foreign_client_cannot_read_balance(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    # Чужой клиент перебором acc-id — должен получить 403, а не баланс
    r = await client.get(f"/accounts/acc-{acc}/balances", headers=auth(client_token("team999-1")))
    assert r.status_code == 403


async def test_foreign_client_cannot_read_transactions(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.get(f"/accounts/acc-{acc}/transactions", headers=auth(client_token("team999-1")))
    assert r.status_code == 403


async def test_team_reads_own_client_balance(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.get(f"/accounts/acc-{acc}/balances", headers=auth(team_token("team218")))
    assert r.status_code == 200, r.text


async def test_other_team_cannot_read_balance(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.get(f"/accounts/acc-{acc}/balances", headers=auth(team_token("team999")))
    assert r.status_code == 403


async def test_foreign_cannot_change_status(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.put(f"/accounts/acc-{acc}/status",
                         headers=auth(client_token("team999-1")), json={"status": "closed"})
    assert r.status_code == 403


async def test_owner_can_change_status(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "500")
    acc = await account_id(session_maker, "AAA")
    r = await client.put(f"/accounts/acc-{acc}/status",
                         headers=auth(client_token("team218-1")), json={"status": "closed"})
    assert r.status_code == 200, r.text
