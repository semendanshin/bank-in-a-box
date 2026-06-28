"""
Тесты входящих межбанковских переводов (/interbank/receive).

Ключевое: идемпотентность (повтор не зачисляет дважды) и аутентификация.
"""
from decimal import Decimal

from _helpers import add_account, get_balance


def _transfer(transfer_id, account, amount="500", from_bank="abank"):
    return {
        "transfer_id": transfer_id,
        "from_bank": from_bank,
        "to_account_number": account,
        "amount": amount,
        "currency": "RUB",
        "description": "test",
    }


HDR = {"x-bank-auth-token": "abank"}


async def test_receive_credits_account(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    r = await client.post("/interbank/receive", headers=HDR,
                          json=_transfer("tr-1", "AAA", "500"))
    assert r.status_code == 201, r.text
    assert r.json()["success"] is True
    assert await get_balance(session_maker, "AAA") == Decimal("600.00")


async def test_receive_is_idempotent(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    first = await client.post("/interbank/receive", headers=HDR,
                              json=_transfer("tr-dup", "AAA", "500"))
    assert first.status_code == 201

    # Повтор с тем же transfer_id — не должен зачислять второй раз
    second = await client.post("/interbank/receive", headers=HDR,
                               json=_transfer("tr-dup", "AAA", "500"))
    assert second.status_code == 200, second.text
    assert second.json()["success"] is True
    assert await get_balance(session_maker, "AAA") == Decimal("600.00")


async def test_receive_requires_auth_token(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    r = await client.post("/interbank/receive", json=_transfer("tr-2", "AAA"))
    assert r.status_code == 401
    assert await get_balance(session_maker, "AAA") == Decimal("100.00")


async def test_receive_negative_amount_rejected(client, session_maker):
    await add_account(session_maker, "team218-1", "AAA", "100")
    r = await client.post("/interbank/receive", headers=HDR,
                          json=_transfer("tr-3", "AAA", "-10"))
    assert r.status_code == 400
    assert await get_balance(session_maker, "AAA") == Decimal("100.00")


async def test_receive_unknown_account_404(client, session_maker):
    r = await client.post("/interbank/receive", headers=HDR,
                          json=_transfer("tr-4", "NOPE", "500"))
    assert r.status_code == 404


async def test_receive_rejects_self_as_sender(client, session_maker):
    # from_bank == BANK_CODE (vbank) недопустим
    await add_account(session_maker, "team218-1", "AAA", "100")
    r = await client.post("/interbank/receive", headers={"x-bank-auth-token": "vbank"},
                          json=_transfer("tr-5", "AAA", "500", from_bank="vbank"))
    assert r.status_code == 400
