"""Тесты оплаты картой: списание, лимиты, статус карты, авторизация."""
from decimal import Decimal

from _helpers import add_account, get_balance, client_token, team_token, auth


async def _card(client, session_maker, owner="team218-1", acc="AAA", balance="1000000"):
    await add_account(session_maker, owner, acc, balance)
    r = await client.post("/cards", headers=auth(client_token(owner)),
                          json={"account_number": acc, "card_type": "debit", "card_name": "Visa"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["cardId"]


async def test_pay_debits_account(client, session_maker):
    cid = await _card(client, session_maker)
    r = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": 1000, "merchant_name": "Пятёрочка", "city": "Москва"})
    assert r.status_code == 200, r.text
    assert await get_balance(session_maker, "AAA") == Decimal("999000.00")


async def test_pay_insufficient_funds(client, session_maker):
    cid = await _card(client, session_maker, balance="500")
    r = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": 1000})
    assert r.status_code == 400
    assert await get_balance(session_maker, "AAA") == Decimal("500.00")


async def test_pay_blocked_card(client, session_maker):
    cid = await _card(client, session_maker)
    await client.put(f"/cards/{cid}/status", headers=auth(client_token("team218-1")),
                     json={"status": "blocked"})
    r = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": 100})
    assert r.status_code == 400


async def test_pay_daily_limit_enforced(client, session_maker):
    cid = await _card(client, session_maker)
    await client.put(f"/cards/{cid}/limits", headers=auth(client_token("team218-1")),
                     json={"daily_limit": 1500})
    a = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": 1000})
    assert a.status_code == 200, a.text
    b = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": 1000})
    assert b.status_code == 400  # 1000 + 1000 > 1500


async def test_pay_foreign_caller_forbidden(client, session_maker):
    cid = await _card(client, session_maker)
    r = await client.post(f"/cards/{cid}/pay?client_id=team218-1",
                          headers=auth(team_token("team999")), json={"amount": 100})
    assert r.status_code == 403


async def test_pay_negative_rejected(client, session_maker):
    cid = await _card(client, session_maker)
    r = await client.post(f"/cards/{cid}/pay", headers=auth(client_token("team218-1")),
                          json={"amount": -5})
    assert r.status_code == 422  # pydantic gt=0
