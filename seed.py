"""
Идемпотентный сидинг демо-данных при старте приложения.

Цель — чтобы банк «ощущался настоящим»: у клиентов есть карты и реалистичная
история транзакций (зарплата + траты по мерчантам с MCC, городом и привязкой
к карте). Данные детерминированы (на одинаковом окружении воспроизводятся),
но выглядят разнообразно. Всё пишется через ORM, поэтому схема всегда
совпадает с моделями.

Параметризация через env:
  TEAM_CLIENT_ID / TEAM_CLIENT_SECRET — команда (POST /auth/bank-token).
  SEED_CLIENTS   — сколько клиентов завести (по умолчанию 2).
  SEED_BALANCE   — текущий баланс счёта (по умолчанию 1000000).
  SEED_TX        — генерировать историю транзакций (по умолчанию true).
  SEED_TX_MONTHS — за сколько последних месяцев (по умолчанию 3).
"""
import os
import hashlib
import random
import uuid
from decimal import Decimal
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from .models import Client, Account, BankCapital, BankSettings, Team, Merchant, Card, Transaction, Product
    from .config import config
except ImportError:
    from models import Client, Account, BankCapital, BankSettings, Team, Merchant, Card, Transaction, Product
    from config import config


BANK_DIGIT = {"vbank": "1", "abank": "2", "sbank": "3"}

TEAM_ID = os.getenv("TEAM_CLIENT_ID", "team200")
TEAM_SECRET = os.getenv("TEAM_CLIENT_SECRET", "5OAaa4DYzYKfnOU6zbR34ic5qMm7VSMB")
SEED_CLIENTS = int(os.getenv("SEED_CLIENTS", "2"))
SEED_BALANCE = Decimal(os.getenv("SEED_BALANCE", "1000000"))
SEED_TX = os.getenv("SEED_TX", "true").lower() == "true"
SEED_TX_MONTHS = int(os.getenv("SEED_TX_MONTHS", "3"))


# Каталог мерчантов: name, legal_name, mcc, category, city (None = онлайн)
MERCHANTS = [
    ("Пятёрочка", "ООО «Агроторг»", "5411", "grocery", "Москва"),
    ("Магнит", "АО «Тандер»", "5411", "grocery", "Краснодар"),
    ("ВкусВилл", "ООО «Вкусвилл»", "5411", "grocery", "Санкт-Петербург"),
    ("Яндекс.Еда", "ООО «Яндекс»", "5812", "restaurant", "Москва"),
    ("KFC", "ООО «Ям Ресторантс»", "5814", "restaurant", "Москва"),
    ("Лукойл АЗС", "ПАО «Лукойл»", "5541", "gas_station", "Москва"),
    ("OZON", "ООО «Интернет Решения»", "5399", "retail", None),
    ("Wildberries", "ООО «Вайлдберриз»", "5399", "retail", None),
    ("Московский метрополитен", "ГУП «Московский метрополитен»", "4111", "transport", "Москва"),
    ("МТС", "ПАО «МТС»", "4814", "telecom", None),
]

# Диапазоны сумм трат по категориям (руб.)
CATEGORY_AMOUNTS = {
    "grocery": (300, 4000),
    "restaurant": (400, 2500),
    "gas_station": (1500, 4000),
    "retail": (500, 15000),
    "transport": (50, 120),
    "telecom": (300, 1500),
}


def _account_number(bank_digit: str, index: int) -> str:
    return f"408178{bank_digit}{index:013d}"


def _rng_for(*parts) -> random.Random:
    """Детерминированный RNG из стабильного хэша (воспроизводимый сид)."""
    raw = ":".join(str(p) for p in parts).encode()
    seed = int.from_bytes(hashlib.md5(raw).digest()[:8], "big")
    return random.Random(seed)


def _card_number(rng: random.Random) -> str:
    base = "427600" + "".join(str(rng.randint(0, 9)) for _ in range(9))
    check = str((10 - sum(int(d) for d in base) % 10) % 10)
    return base + check


async def _seed_merchants(session) -> list:
    """Создать мерчантов (один раз) и вернуть ORM-объекты."""
    merchants = []
    for name, legal, mcc, category, city in MERCHANTS:
        m = Merchant(
            merchant_id=f"merchant-{name.lower().split()[0]}-{uuid.uuid4().hex[:6]}",
            name=name, legal_name=legal, mcc_code=mcc, category=category,
            city=city, country="RUS",
        )
        session.add(m)
        merchants.append(m)
    await session.flush()
    return merchants


async def _seed_history(session, account, client, merchants):
    """Сгенерировать карту и реалистичную историю транзакций по счёту."""
    rng = _rng_for(config.BANK_CODE, client.person_id, "history")
    now = datetime.utcnow()

    # Дебетовая карта, привязанная к счёту
    card = Card(
        card_id=f"card-{uuid.uuid4().hex[:12]}",
        account_id=account.id,
        client_id=client.id,
        card_number=_card_number(rng),
        card_type="debit",
        card_name="Visa Classic",
        holder_name=(client.full_name or client.person_id).upper(),
        expiry_month=rng.randint(1, 12),
        expiry_year=now.year + 3,
        daily_limit=Decimal("100000"),
        monthly_limit=Decimal("500000"),
        status="active",
    )
    session.add(card)
    await session.flush()

    income = Decimal(str(client.monthly_income or "100000"))

    for month in range(SEED_TX_MONTHS, 0, -1):
        month_start = now - timedelta(days=30 * month)

        # Зарплата (входящий перевод)
        session.add(Transaction(
            account_id=account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=income, direction="credit", currency="RUB",
            counterparty="ООО «Работодатель»", description="Заработная плата",
            status="completed", bank_transaction_code="ReceivedCreditTransfer",
            transaction_date=month_start + timedelta(days=5),
            booking_date=month_start + timedelta(days=5),
        ))

        # Траты по карте
        for _ in range(rng.randint(8, 16)):
            name, legal, mcc, category, city = rng.choice(MERCHANTS)
            merchant = next((m for m in merchants if m.name == name), None)
            lo, hi = CATEGORY_AMOUNTS.get(category, (200, 3000))
            amount = Decimal(rng.randint(lo, hi))
            when = month_start + timedelta(
                days=rng.randint(1, 27), hours=rng.randint(8, 22), minutes=rng.randint(0, 59)
            )
            session.add(Transaction(
                account_id=account.id,
                transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                amount=amount, direction="debit", currency="RUB",
                merchant_id=merchant.id if merchant else None,
                card_id=card.id,
                counterparty=name, description=f"Оплата: {name}",
                status="completed", bank_transaction_code="PointOfSale",
                transaction_city=city, transaction_country="RUS" if city else None,
                transaction_date=when, booking_date=when,
            ))


async def seed_if_empty(session: AsyncSession) -> bool:
    """Засидить демо-данные, если БД пуста. Returns True если сид выполнен."""
    existing = await session.execute(select(func.count(Client.id)))
    if (existing.scalar() or 0) > 0:
        return False

    bank_code = config.BANK_CODE
    digit = BANK_DIGIT.get(bank_code, "9")

    # Команда
    team = await session.execute(select(Team).where(Team.client_id == TEAM_ID))
    if team.scalar_one_or_none() is None:
        session.add(Team(
            client_id=TEAM_ID, client_secret=TEAM_SECRET,
            team_name=f"{TEAM_ID} (seed)", is_active=True,
        ))

    # Настройки: авто-одобрение согласий
    for key, value in [
        ("auto_approve_consents", "true"),
        ("auto_approve_payment_consents", "true"),
        ("bank_code", bank_code),
    ]:
        session.add(BankSettings(key=key, value=value))

    # Капитал
    session.add(BankCapital(
        bank_code=bank_code, capital=Decimal("3500000.00"),
        initial_capital=Decimal("3500000.00"),
        total_deposits=Decimal("0"), total_loans=Decimal("0"),
    ))

    # Мерчанты (для реалистичной истории)
    merchants = await _seed_merchants(session) if SEED_TX else []

    # Клиенты + счета (+ карта и история)
    for i in range(1, SEED_CLIENTS + 1):
        client = Client(
            person_id=f"{TEAM_ID}-{i}", client_type="individual",
            full_name=f"{TEAM_ID} клиент №{i}", segment="employee",
            birth_year=1990, monthly_income=Decimal("100000"),
        )
        session.add(client)
        await session.flush()

        account = Account(
            client_id=client.id, account_number=_account_number(digit, i),
            account_type="checking", balance=SEED_BALANCE, currency="RUB", status="active",
        )
        session.add(account)
        await session.flush()

        if SEED_TX:
            await _seed_history(session, account, client, merchants)

    # Каталог продуктов банка (маркетплейс): депозит, кредит, кредитная карта
    products_by_bank = {
        "vbank": [
            ("vbank-dep-1", "deposit", "Вклад «Виртуальный»", "Накопительный вклад с ежемесячной капитализацией", Decimal("16.00"), Decimal("10000"), Decimal("5000000"), 12),
            ("vbank-loan-1", "loan", "Кредит наличными", "Потребительский кредит без залога", Decimal("23.90"), Decimal("30000"), Decimal("3000000"), 36),
            ("vbank-cc-1", "credit_card", "Кредитная карта Virtual", "Кредитка с льготным периодом 120 дней", Decimal("29.90"), None, Decimal("500000"), None),
        ],
        "abank": [
            ("abank-dep-1", "deposit", "Вклад «Awesome Max»", "Вклад с максимальной ставкой", Decimal("17.50"), Decimal("50000"), Decimal("10000000"), 6),
            ("abank-loan-1", "loan", "Автокредит Awesome", "Кредит на новый автомобиль", Decimal("18.50"), Decimal("100000"), Decimal("7000000"), 60),
            ("abank-cc-1", "credit_card", "Awesome Cashback", "Карта с кэшбэком до 10%", Decimal("27.50"), None, Decimal("700000"), None),
        ],
        "sbank": [
            ("sbank-dep-1", "deposit", "Вклад «Smart Save»", "Долгосрочный вклад", Decimal("18.00"), Decimal("10000"), Decimal("8000000"), 24),
            ("sbank-loan-1", "loan", "Ипотека Smart", "Ипотечный кредит на жильё", Decimal("13.90"), Decimal("500000"), Decimal("30000000"), 240),
            ("sbank-cc-1", "credit_card", "Smart Credit Card", "Премиальная кредитная карта", Decimal("25.90"), None, Decimal("1000000"), None),
        ],
    }
    for pid, ptype, pname, pdesc, rate, lo, hi, term in products_by_bank.get(bank_code, []):
        session.add(Product(
            product_id=pid, product_type=ptype, name=pname, description=pdesc,
            interest_rate=rate, min_amount=lo, max_amount=hi, term_months=term, is_active=True,
        ))

    await session.commit()
    return True
