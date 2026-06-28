"""
Тесты Payments API.

Покрывают баги, ронявшие интеграцию:
- обращение к неопределённой current_client (было 500 на каждом платеже);
- Transaction с несуществующими полями (TypeError);
- отсутствие проверки владения счётом и валидации суммы.
"""
from decimal import Decimal

from _helpers import add_account, get_balance, client_token, team_token, auth


def _payment_body(debtor, creditor, amount="300", bank_code=None):
    creditor_acc = {"identification": creditor}
    if bank_code:
        creditor_acc["bank_code"] = bank_code
    return {"data": {"initiation": {
        "instructedAmount": {"amount": amount, "currency": "RUB"},
        "debtorAccount": {"identification": debtor},
        "creditorAccount": creditor_acc,
    }}}


async def test_internal_payment_succeeds(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")

    r = await client.post("/payments", headers=auth(client_token("team218-1")),
                          json=_payment_body("AAA", "BBB", "300"))

    assert r.status_code == 201, r.text
    assert r.json()["data"]["status"] == "AcceptedSettlementCompleted"
    assert await get_balance(session_maker, "AAA") == Decimal("700.00")
    assert await get_balance(session_maker, "BBB") == Decimal("300.00")


async def test_get_payment_returns_200(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")
    created = await client.post("/payments", headers=auth(client_token("team218-1")),
                                json=_payment_body("AAA", "BBB", "100"))
    payment_id = created.json()["data"]["paymentId"]

    r = await client.get(f"/payments/{payment_id}", headers=auth(client_token("team218-1")))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["paymentId"] == payment_id


async def test_negative_amount_rejected(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")
    r = await client.post("/payments", headers=auth(client_token("team218-1")),
                          json=_payment_body("AAA", "BBB", "-50"))
    assert r.status_code == 400
    assert await get_balance(session_maker, "AAA") == Decimal("1000.00")


async def test_zero_amount_rejected(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")
    r = await client.post("/payments", headers=auth(client_token("team218-1")),
                          json=_payment_body("AAA", "BBB", "0"))
    assert r.status_code == 400


async def test_payment_from_foreign_account_forbidden(client, session_maker):
    # AAA принадлежит team218-1, токен — у team218-2
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")
    r = await client.post("/payments", headers=auth(client_token("team218-2")),
                          json=_payment_body("AAA", "BBB", "100"))
    assert r.status_code == 403
    assert await get_balance(session_maker, "AAA") == Decimal("1000.00")


async def test_team_owns_its_clients_accounts(client, session_maker):
    # team-токен команды может платить со счёта своего клиента team218-1
    await add_account(session_maker, "team218-1", "AAA", "1000")
    await add_account(session_maker, "team218-2", "BBB", "0")
    r = await client.post("/payments", headers=auth(team_token("team218")),
                          json=_payment_body("AAA", "BBB", "100"))
    assert r.status_code == 201, r.text


async def test_insufficient_funds_rejected(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    await add_account(session_maker, "team218-2", "BBB", "0")
    r = await client.post("/payments", headers=auth(client_token("team218-1")),
                          json=_payment_body("AAA", "BBB", "99999"))
    assert r.status_code == 400


async def test_missing_token_unauthorized(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000")
    r = await client.post("/payments", json=_payment_body("AAA", "BBB", "100"))
    assert r.status_code in (401, 403)
