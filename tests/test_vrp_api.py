"""
Тесты VRP: применение лимитов и проверка владельца согласия
(раньше лимиты периода/количества не проверялись, владелец не сверялся).
"""
from _helpers import add_account, account_id, client_token, auth


async def _create_consent(client, owner, acc_id, max_individual=10000, max_count=None, max_period=None):
    body = {
        "account_id": f"acc-{acc_id}",
        "max_individual_amount": max_individual,
    }
    if max_count is not None:
        body["max_payments_count"] = max_count
    if max_period is not None:
        body["max_amount_period"] = max_period
    r = await client.post("/vrp-consents", headers=auth(client_token(owner)), json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["consent_id"]


async def _pay(client, owner, consent_id, amount):
    return await client.post("/domestic-vrp-payments", headers=auth(client_token(owner)), json={
        "vrp_consent_id": consent_id,
        "amount": amount,
        "destination_account": "40817820000000000999",
        "is_recurring": False,
    })


async def test_vrp_payment_count_limit(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000000")
    acc_id = await account_id(session_maker, "AAA")
    consent_id = await _create_consent(client, "team218-1", acc_id, max_count=1)

    first = await _pay(client, "team218-1", consent_id, 1000)
    assert first.status_code == 201, first.text

    second = await _pay(client, "team218-1", consent_id, 1000)
    assert second.status_code == 400  # лимит количества исчерпан


async def test_vrp_period_amount_limit(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000000")
    acc_id = await account_id(session_maker, "AAA")
    consent_id = await _create_consent(client, "team218-1", acc_id,
                                       max_individual=10000, max_period=1500)

    ok = await _pay(client, "team218-1", consent_id, 1000)
    assert ok.status_code == 201, ok.text

    # 1000 уже потрачено, лимит периода 1500 -> ещё 1000 не влезает
    over = await _pay(client, "team218-1", consent_id, 1000)
    assert over.status_code == 400


async def test_vrp_individual_limit(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "1000000")
    acc_id = await account_id(session_maker, "AAA")
    consent_id = await _create_consent(client, "team218-1", acc_id, max_individual=500)

    r = await _pay(client, "team218-1", consent_id, 1000)
    assert r.status_code == 400  # больше max_individual_amount


async def test_vrp_consent_ownership(client, session_maker):
    # Согласие создаёт team218-1, платёж пытается инициировать team218-2
    await add_account(session_maker, "team218-1", "AAA", "1000000")
    await add_account(session_maker, "team218-2", "BBB", "1000000")
    acc_id = await account_id(session_maker, "AAA")
    consent_id = await _create_consent(client, "team218-1", acc_id)

    r = await _pay(client, "team218-2", consent_id, 1000)
    assert r.status_code == 403
