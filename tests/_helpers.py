"""Вспомогательные функции для тестов."""
from decimal import Decimal

from sqlalchemy import select

import models
from services.auth_service import create_access_token


def client_token(person_id: str) -> str:
    return create_access_token({"sub": person_id, "type": "client"})


def team_token(team_id: str) -> str:
    return create_access_token({"sub": team_id, "client_id": team_id, "type": "team"})


def banker_token() -> str:
    return create_access_token({"sub": "banker", "type": "banker"})


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def add_account(session_maker, person_id, account_number, balance="0", status="active"):
    """Создать (при необходимости) клиента и счёт. Возвращает account_number."""
    async with session_maker() as s:
        client = (
            await s.execute(select(models.Client).where(models.Client.person_id == person_id))
        ).scalar_one_or_none()
        if client is None:
            client = models.Client(
                person_id=person_id, client_type="individual", full_name=person_id
            )
            s.add(client)
            await s.flush()
        s.add(models.Account(
            client_id=client.id,
            account_number=account_number,
            account_type="checking",
            balance=Decimal(str(balance)),
            status=status,
        ))
        await s.commit()
    return account_number


async def get_balance(session_maker, account_number) -> Decimal:
    async with session_maker() as s:
        acc = (
            await s.execute(select(models.Account).where(models.Account.account_number == account_number))
        ).scalar_one()
        return acc.balance


async def account_id(session_maker, account_number) -> int:
    async with session_maker() as s:
        acc = (
            await s.execute(select(models.Account).where(models.Account.account_number == account_number))
        ).scalar_one()
        return acc.id
