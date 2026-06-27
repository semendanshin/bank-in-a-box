"""
Идемпотентный сидинг демо-данных при старте приложения.

Сид выполняется через ORM-модели (а не через сырой SQL), поэтому схема
БД всегда совпадает с моделями — это убирает рассинхронизацию, которая
возникала при использовании частичного init.sql вместе с create_all.

Данные параметризованы кодом банка (config.BANK_CODE), чтобы можно было
поднять несколько банков из одного образа: у каждого банка свой диапазон
номеров счетов (различающая цифра в 7-й позиции), но одни и те же person_id
клиентов — это позволяет демонстрировать межбанковские переводы и агрегацию.
"""
import os
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from .models import Client, Account, BankCapital, BankSettings, Team, Product
    from .config import config
except ImportError:
    from models import Client, Account, BankCapital, BankSettings, Team, Product
    from config import config


# Различающая цифра банка в номере счёта (7-я позиция).
# Для неизвестных банков берём 9 — лишь бы счета были уникальны в своей БД.
BANK_DIGIT = {"vbank": "1", "abank": "2", "sbank": "3"}

# Креды команды по умолчанию (совпадают с дефолтами multibank_proxy и README).
DEFAULT_TEAM_ID = os.getenv("TEAM_CLIENT_ID", "team200")
DEFAULT_TEAM_SECRET = os.getenv("TEAM_CLIENT_SECRET", "5OAaa4DYzYKfnOU6zbR34ic5qMm7VSMB")


def _account_number(bank_digit: str, index: int) -> str:
    """Сгенерировать 20-значный номер счёта, уникальный в рамках банка."""
    return f"408178{bank_digit}{index:013d}"


async def seed_if_empty(session: AsyncSession) -> bool:
    """
    Засидить демо-данные, если БД ещё пуста.

    Returns:
        True если сид выполнен, False если данные уже были.
    """
    existing = await session.execute(select(func.count(Client.id)))
    if (existing.scalar() or 0) > 0:
        return False

    bank_code = config.BANK_CODE
    digit = BANK_DIGIT.get(bank_code, "9")

    # --- Команда (для POST /auth/bank-token) ---
    team = await session.execute(select(Team).where(Team.client_id == DEFAULT_TEAM_ID))
    if team.scalar_one_or_none() is None:
        session.add(Team(
            client_id=DEFAULT_TEAM_ID,
            client_secret=DEFAULT_TEAM_SECRET,
            team_name=f"{DEFAULT_TEAM_ID} (seed)",
            is_active=True,
        ))

    # --- Настройки банка: авто-одобрение согласий (turnkey межбанк) ---
    for key, value in [
        ("auto_approve_consents", "true"),
        ("auto_approve_payment_consents", "true"),
        ("bank_code", bank_code),
    ]:
        session.add(BankSettings(key=key, value=value))

    # --- Капитал банка ---
    session.add(BankCapital(
        bank_code=bank_code,
        capital=Decimal("3500000.00"),
        initial_capital=Decimal("3500000.00"),
        total_deposits=Decimal("0"),
        total_loans=Decimal("0"),
    ))

    # --- Клиенты команды (одинаковые person_id во всех банках) ---
    index = 1
    base_balances = [500000, 450000, 480000, 600000, 350000,
                     320000, 550000, 280000, 750000, 420000]
    for i in range(1, 11):
        client = Client(
            person_id=f"{DEFAULT_TEAM_ID}-{i}",
            client_type="individual",
            full_name=f"{DEFAULT_TEAM_ID} участник №{i}",
            segment="employee",
            birth_year=1990 + (i % 10),
            monthly_income=Decimal("100000"),
        )
        session.add(client)
        await session.flush()  # получить client.id
        session.add(Account(
            client_id=client.id,
            account_number=_account_number(digit, index),
            account_type="checking",
            balance=Decimal(str(base_balances[i - 1])),
            currency="RUB",
            status="active",
        ))
        index += 1

    # --- Демо-клиенты ---
    for i in range(1, 4):
        client = Client(
            person_id=f"{bank_code}-demo-{i}",
            client_type="individual",
            full_name=f"Демо клиент {bank_code} №{i}",
            segment="employee",
            birth_year=1985 + i,
            monthly_income=Decimal("120000"),
        )
        session.add(client)
        await session.flush()
        session.add(Account(
            client_id=client.id,
            account_number=_account_number(digit, index),
            account_type="checking",
            balance=Decimal("300000.00"),
            currency="RUB",
            status="active",
        ))
        index += 1

    # --- Базовый каталог продуктов ---
    session.add_all([
        Product(product_id=f"prod-{bank_code}-deposit-001", product_type="deposit",
                name="Депозит", description="Срочный вклад", interest_rate=Decimal("9.00"),
                min_amount=Decimal("10000"), term_months=12, is_active=True),
        Product(product_id=f"prod-{bank_code}-loan-001", product_type="loan",
                name="Кредит наличными", description="Потребительский кредит",
                interest_rate=Decimal("13.50"), min_amount=Decimal("50000"),
                term_months=24, is_active=True),
    ])

    await session.commit()
    return True
