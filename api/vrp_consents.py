"""
VRP Consents API - Согласия на периодические переводы
OpenBanking Russia VRP API v1.3.1
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal
from datetime import datetime, timedelta
import uuid

from database import get_db
from models import VRPConsent, Account, Client
from services.auth_service import require_client
from utils import parse_account_id

router = APIRouter(
    prefix="/vrp-consents",
    tags=["04 OpenBanking: VRP Consents"],
    include_in_schema=False  # Скрыто из публичной документации
)


# === Pydantic Models ===

class VRPConsentRequest(BaseModel):
    """Запрос на создание VRP согласия"""
    account_id: str
    max_individual_amount: float
    max_amount_period: Optional[float] = None
    period_type: Optional[str] = "month"  # day, week, month, year
    max_payments_count: Optional[int] = None
    valid_days: Optional[int] = 365


# === Endpoints ===

@router.post("", status_code=201)
async def create_vrp_consent(
    request: VRPConsentRequest,
    current_client: dict = Depends(require_client),
    db: AsyncSession = Depends(get_db)
):
    """
    ## 🔄 Создание согласия на VRP (периодические переводы)
    
    **OpenBanking Russia VRP API v1.3.1 - Variable Recurring Payments**
    
    ### Что такое VRP?
    Периодические платежи с **переменными реквизитами** — это автоматические переводы, где:
    - Получатель может меняться
    - Сумма может меняться
    - Но есть строгие лимиты безопасности
    
    ### Примеры использования:
    - 📱 Автооплата мобильной связи (сумма меняется каждый месяц)
    - 🏠 Оплата ЖКХ (переменная сумма)
    - 🚗 Подписки на сервисы (разные суммы за разные планы)
    - 💳 Автопополнение баланса (когда баланс < порога)
    
    ### Пример запроса:
    ```json
    {
      "account_id": "acc-123",
      "max_individual_amount": 5000.0,
      "max_amount_period": 20000.0,
      "period_type": "month",
      "max_payments_count": 100,
      "valid_days": 365
    }
    ```
    
    ### Параметры лимитов:
    - `max_individual_amount` — максимум за один платеж (₽)
    - `max_amount_period` — максимум за период (₽)
    - `period_type` — период: `day`, `week`, `month`, `year`
    - `max_payments_count` — максимальное количество платежей
    - `valid_days` — срок действия согласия (дней)
    
    ### Процесс:
    1. **Создание согласия** → `POST /vrp-consents` (этот endpoint)
    2. **Инициация платежей** → `POST /domestic-vrp-payments`
    3. **Проверка истории** → `GET /domestic-vrp-payments`
    
    ### ⚠️ Безопасность:
    - Клиент явно дает согласие с лимитами
    - Каждый платеж проверяется на соответствие лимитам
    - Согласие можно отозвать в любой момент: `DELETE /vrp-consents/{consent_id}`
    - При превышении лимита платеж будет отклонен
    
    ### Статусы согласия:
    - `active` — действующее
    - `revoked` — отозвано клиентом
    - `expired` — истек срок
    """
    if not current_client:
        raise HTTPException(401, "Unauthorized")
    
    # Найти клиента
    result = await db.execute(
        select(Client).where(Client.person_id == current_client["client_id"])
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(404, "Client not found")
    
    # Найти счет
    account_id_int = parse_account_id(request.account_id)
    account_result = await db.execute(
        select(Account).where(
            Account.id == account_id_int,
            Account.client_id == client.id
        )
    )
    account = account_result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(404, "Account not found or not owned by client")
    
    # Создать согласие
    consent_id = f"vrp-{uuid.uuid4().hex[:12]}"
    valid_from = datetime.utcnow()
    valid_to = valid_from + timedelta(days=request.valid_days)
    
    consent = VRPConsent(
        consent_id=consent_id,
        client_id=client.id,
        account_id=account.id,
        status="Authorised",  # Для упрощения сразу авторизуем
        max_individual_amount=Decimal(str(request.max_individual_amount)),
        max_amount_period=Decimal(str(request.max_amount_period)) if request.max_amount_period else None,
        period_type=request.period_type,
        max_payments_count=request.max_payments_count,
        valid_from=valid_from,
        valid_to=valid_to,
        authorised_at=datetime.utcnow()
    )
    
    db.add(consent)
    await db.commit()
    await db.refresh(consent)
    
    return {
        "data": {
            "consent_id": consent.consent_id,
            "account_id": f"acc-{account.id}",
            "account_number": account.account_number,
            "status": consent.status,
            "max_individual_amount": float(consent.max_individual_amount),
            "max_amount_period": float(consent.max_amount_period) if consent.max_amount_period else None,
            "period_type": consent.period_type,
            "max_payments_count": consent.max_payments_count,
            "valid_from": consent.valid_from.isoformat() + "Z",
            "valid_to": consent.valid_to.isoformat() + "Z",
            "created_at": consent.created_at.isoformat() + "Z"
        },
        "links": {
            "self": f"/vrp-consents/{consent.consent_id}"
        },
        "meta": {
            "message": "VRP Consent created and authorised successfully"
        }
    }


@router.get("/{consent_id}")
async def get_vrp_consent(
    consent_id: str,
    current_client: dict = Depends(require_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Получить детали VRP согласия
    
    OpenBanking Russia VRP API v1.3.1
    GET /vrp-consents/{consentId}
    """
    if not current_client:
        raise HTTPException(401, "Unauthorized")
    
    # Найти клиента
    result = await db.execute(
        select(Client).where(Client.person_id == current_client["client_id"])
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(404, "Client not found")
    
    # Найти согласие
    consent_result = await db.execute(
        select(VRPConsent, Account).join(
            Account, VRPConsent.account_id == Account.id
        ).where(
            VRPConsent.consent_id == consent_id,
            VRPConsent.client_id == client.id
        )
    )
    
    consent_data = consent_result.first()
    
    if not consent_data:
        raise HTTPException(404, "VRP Consent not found")
    
    consent, account = consent_data
    
    return {
        "data": {
            "consent_id": consent.consent_id,
            "account_id": f"acc-{account.id}",
            "account_number": account.account_number,
            "status": consent.status,
            "max_individual_amount": float(consent.max_individual_amount),
            "max_amount_period": float(consent.max_amount_period) if consent.max_amount_period else None,
            "period_type": consent.period_type,
            "max_payments_count": consent.max_payments_count,
            "valid_from": consent.valid_from.isoformat() + "Z" if consent.valid_from else None,
            "valid_to": consent.valid_to.isoformat() + "Z" if consent.valid_to else None,
            "created_at": consent.created_at.isoformat() + "Z",
            "authorised_at": consent.authorised_at.isoformat() + "Z" if consent.authorised_at else None,
            "revoked_at": consent.revoked_at.isoformat() + "Z" if consent.revoked_at else None
        },
        "links": {
            "self": f"/vrp-consents/{consent_id}"
        }
    }


@router.delete("/{consent_id}")
async def delete_vrp_consent(
    consent_id: str,
    current_client: dict = Depends(require_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Отозвать VRP согласие
    
    OpenBanking Russia VRP API v1.3.1
    DELETE /vrp-consents/{consentId}
    """
    if not current_client:
        raise HTTPException(401, "Unauthorized")
    
    # Найти клиента
    result = await db.execute(
        select(Client).where(Client.person_id == current_client["client_id"])
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(404, "Client not found")
    
    # Найти согласие
    consent_result = await db.execute(
        select(VRPConsent).where(
            VRPConsent.consent_id == consent_id,
            VRPConsent.client_id == client.id
        )
    )
    
    consent = consent_result.scalar_one_or_none()
    
    if not consent:
        raise HTTPException(404, "VRP Consent not found")
    
    # Отозвать согласие
    consent.status = "Revoked"
    consent.revoked_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "data": {
            "consent_id": consent.consent_id,
            "status": "Revoked",
            "revoked_at": consent.revoked_at.isoformat() + "Z"
        },
        "meta": {
            "message": "VRP Consent revoked successfully"
        }
    }

