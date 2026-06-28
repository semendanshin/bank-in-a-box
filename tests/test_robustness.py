"""Тесты устойчивости/корректности после доп. правок."""
from sqlalchemy import select

import models
from _helpers import add_account, client_token, banker_token, auth


async def test_malformed_account_id_returns_400(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    r = await client.get("/accounts/acc-not-a-number/balances",
                         headers=auth(client_token("team218-1")))
    assert r.status_code == 400  # не 500


async def test_card_requires_active_account(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100", status="closed")
    r = await client.post("/cards", headers=auth(client_token("team218-1")),
                          json={"account_number": "AAA", "card_type": "debit", "card_name": "V"})
    assert r.status_code == 404


async def test_banker_approve_creates_active_consent(client, session_maker):
    # Выключаем авто-одобрение, чтобы запрос остался pending
    async with session_maker() as s:
        s.add(models.Client(person_id="team218-1", client_type="individual", full_name="x"))
        s.add(models.BankSettings(key="auto_approve_consents", value="false"))
        await s.commit()

    r = await client.post("/account-consents/request",
                          headers={"x-requesting-bank": "team999"},
                          json={"client_id": "team218-1", "permissions": ["ReadBalances"],
                                "requesting_bank": "team999"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    request_id = r.json()["request_id"]

    a = await client.put(f"/banker/consents/{request_id}/approve", headers=auth(banker_token()))
    assert a.status_code == 200, a.text
    assert a.json()["data"]["consent_id"] is not None

    # Согласие реально создано и активно
    async with session_maker() as s:
        consents = (await s.execute(select(models.Consent))).scalars().all()
    assert len(consents) == 1
    assert consents[0].status == "active"
    assert consents[0].granted_to == "team999"
