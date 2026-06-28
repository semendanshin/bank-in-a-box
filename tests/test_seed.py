"""Тесты сидинга: реалистичные данные и идемпотентность."""
from sqlalchemy import select, func

import models
import seed
from seed import seed_if_empty
from _helpers import client_token, auth


async def test_seed_creates_realistic_data(session_maker):
    async with session_maker() as s:
        assert await seed_if_empty(s) is True

    async with session_maker() as s:
        clients = (await s.execute(select(func.count(models.Client.id)))).scalar()
        accounts = (await s.execute(select(func.count(models.Account.id)))).scalar()
        merchants = (await s.execute(select(func.count(models.Merchant.id)))).scalar()
        cards = (await s.execute(select(func.count(models.Card.id)))).scalar()
        txs = (await s.execute(select(func.count(models.Transaction.id)))).scalar()
        with_merchant = (await s.execute(
            select(func.count(models.Transaction.id)).where(models.Transaction.merchant_id.isnot(None))
        )).scalar()

    assert clients == seed.SEED_CLIENTS
    assert accounts == seed.SEED_CLIENTS
    assert cards == seed.SEED_CLIENTS
    assert merchants == len(seed.MERCHANTS)
    assert txs > 0
    assert with_merchant > 0  # есть траты по мерчантам


async def test_seed_is_idempotent(session_maker):
    async with session_maker() as s:
        assert await seed_if_empty(s) is True
    async with session_maker() as s:
        assert await seed_if_empty(s) is False


async def test_transactions_endpoint_enriched(client, session_maker):
    async with session_maker() as s:
        await seed_if_empty(s)
        acc = (await s.execute(
            select(models.Account).join(models.Client)
            .where(models.Client.person_id == f"{seed.TEAM_ID}-1")
        )).scalars().first()
        acc_id = acc.id

    r = await client.get(f"/accounts/acc-{acc_id}/transactions",
                         headers=auth(client_token(f"{seed.TEAM_ID}-1")))
    assert r.status_code == 200, r.text
    txs = r.json()["data"]["transaction"]
    assert len(txs) > 0
    # хотя бы у одной транзакции заполнены мерчант и карта
    assert any(t.get("merchant") for t in txs)
    assert any(t.get("card") for t in txs)
