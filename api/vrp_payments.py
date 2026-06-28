"""
VRP Payments API - Периодические платежи с переменными реквизитами
OpenBanking Russia VRP API v1.3.1
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
import uuid

from database import get_db
from models import VRPPayment, VRPConsent, Account, Transaction, Client
from services.auth_service import require_client, caller_owns_client
from services.payment_service import PaymentService

router = APIRouter(
    prefix="/domestic-vrp-payments",
    tags=["05 OpenBanking: VRP Payments"],
    include_in_schema=False  # Скрыто из публичной документации
)


# === Pydantic Models ===

class VRPPaymentRequest(BaseModel):
    """Запрос на создание VRP платежа"""
    vrp_consent_id: str
    amount: float
    destination_account: str
    destination_bank: Optional[str] = None
    description: Optional[str] = None
    is_recurring: Optional[bool] = True
    recurrence_frequency: Optional[str] = "monthly"  # daily, weekly, monthly


# === Endpoints ===

@router.post("", status_code=201)
async def create_vrp_payment(
    request: VRPPaymentRequest,
    current_client: dict = Depends(require_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Создать периодический платеж по VRP согласию
    
    OpenBanking Russia VRP API v1.3.1
    POST /domestic-vrp-payments
    
    Инициирует платеж на основе ранее созданного VRP согласия с проверкой лимитов.
    """
    if not current_client:
        raise HTTPException(401, "Unauthorized")
    
    # Найти VRP согласие
    consent_result = await db.execute(
        select(VRPConsent, Account).join(
            Account, VRPConsent.account_id == Account.id
        ).where(VRPConsent.consent_id == request.vrp_consent_id)
    )
    
    consent_data = consent_result.first()
    
    if not consent_data:
        raise HTTPException(404, "VRP Consent not found")
    
    consent, account = consent_data

    # Проверить, что согласие принадлежит текущему клиенту
    # (иначе любой клиент мог бы инициировать платёж по чужому согласию)
    client_result = await db.execute(
        select(Client).where(Client.person_id == current_client["client_id"])
    )
    client = client_result.scalar_one_or_none()
    if not client or consent.client_id != client.id:
        raise HTTPException(403, "VRP Consent does not belong to the authenticated client")

    # Проверить статус согласия
    if consent.status != "Authorised":
        raise HTTPException(400, f"VRP Consent is not authorised. Status: {consent.status}")

    # Проверить срок действия
    if consent.valid_to and datetime.utcnow() > consent.valid_to:
        consent.status = "Expired"
        await db.commit()
        raise HTTPException(400, "VRP Consent has expired")

    # Валидация суммы
    try:
        amount = Decimal(str(request.amount))
    except (InvalidOperation, TypeError):
        raise HTTPException(400, "Invalid amount format")
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    # Проверить лимит на одну транзакцию
    if consent.max_individual_amount and amount > consent.max_individual_amount:
        raise HTTPException(
            400,
            f"Amount {amount} exceeds max individual amount {consent.max_individual_amount}"
        )

    # Проверить лимит количества платежей по согласию
    if consent.max_payments_count:
        count_result = await db.execute(
            select(func.count(VRPPayment.id)).where(
                VRPPayment.vrp_consent_id == consent.consent_id,
                VRPPayment.status != "Rejected"
            )
        )
        payments_count = count_result.scalar() or 0
        if payments_count >= consent.max_payments_count:
            raise HTTPException(
                400,
                f"VRP payments count limit reached ({consent.max_payments_count})"
            )

    # Проверить лимит суммы за период (скользящее окно от текущего момента)
    if consent.max_amount_period:
        period_deltas = {
            "day": timedelta(days=1),
            "week": timedelta(weeks=1),
            "month": timedelta(days=30),
            "year": timedelta(days=365),
        }
        period_start = datetime.utcnow() - period_deltas.get(
            consent.period_type, timedelta(days=30)
        )
        sum_result = await db.execute(
            select(func.coalesce(func.sum(VRPPayment.amount), 0)).where(
                VRPPayment.vrp_consent_id == consent.consent_id,
                VRPPayment.status != "Rejected",
                VRPPayment.executed_at >= period_start
            )
        )
        spent_in_period = Decimal(str(sum_result.scalar() or 0))
        if spent_in_period + amount > consent.max_amount_period:
            raise HTTPException(
                400,
                f"Amount {amount} exceeds remaining period limit. "
                f"Spent: {spent_in_period}, Limit: {consent.max_amount_period}"
            )

    # Проверить баланс
    if account.balance < amount:
        raise HTTPException(
            400,
            f"Insufficient funds. Available: {account.balance}, Required: {amount}"
        )
    
    # Создать платеж
    payment_id = f"vrp-pay-{uuid.uuid4().hex[:12]}"

    # Реальное движение средств через PaymentService: деньги действительно
    # зачисляются получателю (внутри банка) или уходят межбанком, с откатом
    # при неудаче — а не просто списываются «в никуда».
    try:
        payment, _interbank = await PaymentService.initiate_payment(
            db=db,
            from_account_number=account.account_number,
            to_account_number=request.destination_account,
            amount=amount,
            description=request.description or f"VRP Payment to {request.destination_account}",
            target_bank_hint=request.destination_bank,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    if payment.status != "AcceptedSettlementCompleted":
        raise HTTPException(400, "VRP payment could not be completed (transfer rejected)")

    # Определить дату следующего платежа
    next_payment_date = None
    if request.is_recurring:
        if request.recurrence_frequency == "daily":
            next_payment_date = datetime.utcnow() + timedelta(days=1)
        elif request.recurrence_frequency == "weekly":
            next_payment_date = datetime.utcnow() + timedelta(weeks=1)
        elif request.recurrence_frequency == "monthly":
            next_payment_date = datetime.utcnow() + timedelta(days=30)
    
    # Создать VRP платеж
    vrp_payment = VRPPayment(
        payment_id=payment_id,
        vrp_consent_id=consent.consent_id,
        account_id=account.id,
        amount=amount,
        destination_account=request.destination_account,
        destination_bank=request.destination_bank,
        description=request.description,
        status="AcceptedSettlementCompleted",  # Мгновенное выполнение для упрощения
        is_recurring=request.is_recurring,
        recurrence_frequency=request.recurrence_frequency,
        next_payment_date=next_payment_date,
        executed_at=datetime.utcnow()
    )
    
    db.add(vrp_payment)
    await db.commit()
    await db.refresh(vrp_payment)
    
    return {
        "data": {
            "payment_id": vrp_payment.payment_id,
            "vrp_consent_id": vrp_payment.vrp_consent_id,
            "account_id": f"acc-{account.id}",
            "amount": float(vrp_payment.amount),
            "currency": vrp_payment.currency,
            "destination_account": vrp_payment.destination_account,
            "destination_bank": vrp_payment.destination_bank,
            "description": vrp_payment.description,
            "status": vrp_payment.status,
            "is_recurring": vrp_payment.is_recurring,
            "recurrence_frequency": vrp_payment.recurrence_frequency,
            "next_payment_date": vrp_payment.next_payment_date.isoformat() + "Z" if vrp_payment.next_payment_date else None,
            "creation_date_time": vrp_payment.creation_date_time.isoformat() + "Z",
            "executed_at": vrp_payment.executed_at.isoformat() + "Z" if vrp_payment.executed_at else None
        },
        "links": {
            "self": f"/domestic-vrp-payments/{vrp_payment.payment_id}"
        },
        "meta": {
            "message": "VRP Payment executed successfully"
        }
    }


@router.get("/{payment_id}")
async def get_vrp_payment(
    payment_id: str,
    current_client: dict = Depends(require_client),
    db: AsyncSession = Depends(get_db)
):
    """
    Получить статус VRP платежа
    
    OpenBanking Russia VRP API v1.3.1
    GET /domestic-vrp-payments/{paymentId}
    """
    if not current_client:
        raise HTTPException(401, "Unauthorized")
    
    # Найти платеж
    result = await db.execute(
        select(VRPPayment, Account).join(
            Account, VRPPayment.account_id == Account.id
        ).where(VRPPayment.payment_id == payment_id)
    )
    
    payment_data = result.first()
    
    if not payment_data:
        raise HTTPException(404, "VRP Payment not found")

    payment, account = payment_data

    # Платёж может смотреть только владелец счёта
    owner = (await db.execute(
        select(Client).where(Client.id == account.client_id)
    )).scalar_one_or_none()
    if not owner or not caller_owns_client(current_client, owner.person_id):
        raise HTTPException(403, "Access denied")

    return {
        "data": {
            "payment_id": payment.payment_id,
            "vrp_consent_id": payment.vrp_consent_id,
            "account_id": f"acc-{account.id}",
            "account_number": account.account_number,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "destination_account": payment.destination_account,
            "destination_bank": payment.destination_bank,
            "description": payment.description,
            "status": payment.status,
            "is_recurring": payment.is_recurring,
            "recurrence_frequency": payment.recurrence_frequency,
            "next_payment_date": payment.next_payment_date.isoformat() + "Z" if payment.next_payment_date else None,
            "creation_date_time": payment.creation_date_time.isoformat() + "Z",
            "status_update_date_time": payment.status_update_date_time.isoformat() + "Z",
            "executed_at": payment.executed_at.isoformat() + "Z" if payment.executed_at else None
        },
        "links": {
            "self": f"/domestic-vrp-payments/{payment_id}"
        }
    }

