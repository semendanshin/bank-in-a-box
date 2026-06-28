"""Тест закрытия депозита: остаток вклада возвращается клиенту, а не теряется."""
from decimal import Decimal

import models
from _helpers import account_id, get_balance, client_token, auth


async def test_deposit_close_returns_funds(client, session_maker):
    async with session_maker() as s:
        c = models.Client(person_id="team218-1", client_type="individual", full_name="x")
        s.add(c)
        await s.flush()
        s.add(models.Account(client_id=c.id, account_number="CHK", account_type="checking",
                             balance=Decimal("100000"), status="active"))
        s.add(models.Product(product_id="dep1", product_type="deposit", name="Депозит",
                             min_amount=Decimal("1000"), is_active=True))
        await s.commit()

    chk = await account_id(session_maker, "CHK")
    tok = client_token("team218-1")

    # Открыть депозит на 50000 из checking
    r = await client.post("/product-agreements", headers=auth(tok),
                          json={"product_id": "dep1", "amount": 50000, "source_account_id": f"acc-{chk}"})
    assert r.status_code == 200, r.text
    agreement_id = r.json()["data"]["agreement_id"]
    assert await get_balance(session_maker, "CHK") == Decimal("50000.00")

    # Закрыть депозит -> 50000 возвращаются на checking
    r = await client.request("DELETE", f"/product-agreements/{agreement_id}",
                             headers=auth(tok), json={"repayment_account_id": f"acc-{chk}"})
    assert r.status_code == 200, r.text
    assert await get_balance(session_maker, "CHK") == Decimal("100000.00")
