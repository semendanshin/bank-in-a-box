"""Тесты Cards API: авторизация и неразглашение полного номера карты."""
from _helpers import add_account, client_token, team_token, auth


async def _make_card(client, session_maker, owner="team218-1", acc="AAA"):
    await add_account(session_maker, owner, acc, "1000")
    r = await client.post("/cards", headers=auth(client_token(owner)),
                          json={"account_number": acc, "card_type": "debit", "card_name": "Visa"})
    assert r.status_code == 200, r.text
    return r.json()["data"]["cardId"]


async def test_owner_lists_own_cards(client, session_maker):
    await _make_card(client, session_maker)
    r = await client.get("/cards", headers=auth(client_token("team218-1")))
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1


async def test_other_team_cannot_list_foreign_cards(client, session_maker):
    await _make_card(client, session_maker)
    # Чужая команда пытается прочитать карты team218-1 через client_id
    r = await client.get("/cards?client_id=team218-1", headers=auth(team_token("team999")))
    assert r.status_code == 403


async def test_full_pan_hidden_from_team_token(client, session_maker):
    card_id = await _make_card(client, session_maker)
    # team218 владеет team218-1, но полный номер ему не отдаём
    r = await client.get(f"/cards/{card_id}?client_id=team218-1&show_full_number=true",
                         headers=auth(team_token("team218")))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["cardNumberFull"] is None


async def test_owner_sees_full_pan(client, session_maker):
    card_id = await _make_card(client, session_maker)
    r = await client.get(f"/cards/{card_id}?show_full_number=true",
                         headers=auth(client_token("team218-1")))
    assert r.status_code == 200
    assert r.json()["data"]["cardNumberFull"] is not None


async def test_other_team_cannot_delete_card(client, session_maker):
    card_id = await _make_card(client, session_maker)
    r = await client.delete(f"/cards/{card_id}?client_id=team218-1",
                            headers=auth(team_token("team999")))
    assert r.status_code == 403
