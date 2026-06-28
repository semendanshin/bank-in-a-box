"""
Interbank API - Прием межбанковских переводов
Используется для коммуникации между банками
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime
from decimal import Decimal
import uuid

from database import get_db
from models import Account, Payment, Transaction, InterbankTransfer, BankCapital
from services.payment_service import PaymentService
from config import config


router = APIRouter(prefix="/interbank", tags=["Interbank API"], include_in_schema=False)


# === Pydantic Models ===

class InterbankTransferRequest(BaseModel):
    """Запрос на входящий межбанковский перевод"""
    transfer_id: str = Field(..., description="ID перевода из банка-отправителя")
    from_bank: str = Field(..., description="Код банка-отправителя")
    to_account_number: str = Field(..., description="Номер счета получателя")
    amount: str = Field(..., description="Сумма перевода")
    currency: str = Field(default="RUB", description="Валюта")
    description: Optional[str] = Field(default="", description="Описание платежа")


class InterbankTransferResponse(BaseModel):
    """Ответ на запрос межбанковского перевода"""
    success: bool
    transfer_id: str
    message: str
    credited_at: Optional[str] = None


# === Endpoints ===

@router.post("/receive", response_model=InterbankTransferResponse, status_code=201)
async def receive_interbank_transfer(
    request: InterbankTransferRequest,
    response: Response,
    x_bank_auth_token: Optional[str] = Header(None, alias="x-bank-auth-token"),
    db: AsyncSession = Depends(get_db)
):
    """
    ## 🏦 Прием входящего межбанковского перевода
    
    Этот endpoint вызывается другим банком для зачисления денег на счет клиента.
    
    ### Процесс:
    1. Найти счет получателя по номеру
    2. Зачислить деньги на счет
    3. Создать транзакцию (Credit - зачисление)
    4. Обновить капитал банка (+amount)
    5. Сохранить запись InterbankTransfer
    
    ### Безопасность:
    - Header `x-bank-auth-token` для аутентификации банка (в MVP упрощено)
    - В продакшене: JWT подписанный ключом банка-отправителя
    
    ### Пример запроса:
    ```json
    {
      "transfer_id": "transfer-abc123",
      "from_bank": "vbank",
      "to_account_number": "40817810099910001234",
      "amount": "5000.00",
      "currency": "RUB",
      "description": "Межбанковский перевод"
    }
    ```
    """
    
    # --- Аутентификация вызывающего банка ---
    # Если задан общий секрет — требуем точного совпадения.
    # Иначе (MVP) — требуем хотя бы наличие токена, чтобы отсечь
    # полностью анонимные запросы (иначе любой мог бы "напечатать" деньги).
    if config.INTERBANK_SHARED_SECRET:
        if x_bank_auth_token != config.INTERBANK_SHARED_SECRET:
            raise HTTPException(401, "Invalid interbank auth token")
    elif not x_bank_auth_token:
        raise HTTPException(401, "Missing x-bank-auth-token")

    # Базовая валидация отправителя
    if not request.from_bank:
        raise HTTPException(400, "from_bank is required")
    if request.from_bank == config.BANK_CODE:
        raise HTTPException(400, "from_bank cannot be this bank")

    # --- Идемпотентность ---
    # Повторный вызов с тем же transfer_id (например, ретрай после таймаута
    # на стороне отправителя) НЕ должен зачислять деньги повторно.
    existing = await db.execute(
        select(InterbankTransfer).where(InterbankTransfer.transfer_id == request.transfer_id)
    )
    existing_transfer = existing.scalar_one_or_none()
    if existing_transfer:
        response.status_code = 200
        return InterbankTransferResponse(
            success=True,
            transfer_id=request.transfer_id,
            message="Transfer already processed (idempotent replay)",
            credited_at=existing_transfer.completed_at.isoformat() + "Z" if existing_transfer.completed_at else None
        )

    try:
        amount = Decimal(request.amount)

        if amount <= 0:
            raise HTTPException(400, "Amount must be positive")

        # 1. Найти счет получателя (с блокировкой строки)
        result = await db.execute(
            select(Account)
            .where(Account.account_number == request.to_account_number)
            .with_for_update()
        )
        to_account = result.scalar_one_or_none()

        if not to_account:
            raise HTTPException(404, f"Account {request.to_account_number} not found in {config.BANK_CODE}")

        if to_account.status and to_account.status != "active":
            raise HTTPException(400, f"Account {request.to_account_number} is not active")

        # 2. Зачислить деньги на счет
        to_account.balance += amount
        
        # 3. Создать транзакцию (Credit - зачисление)
        transaction = Transaction(
            account_id=to_account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=amount,
            direction="credit",
            description=f"Входящий перевод из {request.from_bank}: {request.description}",
            transaction_date=datetime.utcnow()
        )
        db.add(transaction)
        
        # 4. Обновить капитал банка-получателя (+amount)
        await PaymentService.update_bank_capital(
            db=db,
            amount_change=amount,
            reason=f"Incoming transfer from {request.from_bank}: {request.transfer_id}"
        )
        
        # 5. Сохранить запись InterbankTransfer
        interbank_transfer = InterbankTransfer(
            transfer_id=request.transfer_id,
            payment_id=None,  # На стороне получателя нет payment
            from_bank=request.from_bank,
            to_bank=config.BANK_CODE,
            amount=amount,
            status="completed",
            completed_at=datetime.utcnow()
        )
        db.add(interbank_transfer)
        
        await db.commit()
        await db.refresh(to_account)
        
        return InterbankTransferResponse(
            success=True,
            transfer_id=request.transfer_id,
            message=f"Transfer completed successfully. Credited to account {request.to_account_number}",
            credited_at=datetime.utcnow().isoformat() + "Z"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(400, f"Failed to process transfer: {str(e)}")


@router.get("/check-account/{account_number}")
async def check_account_exists(
    account_number: str,
    x_bank_auth_token: Optional[str] = Header(None, alias="x-bank-auth-token"),
    db: AsyncSession = Depends(get_db)
):
    """
    ## 🔍 Проверка существования счета
    
    Используется другими банками для проверки существования счета перед переводом.
    
    ### Возвращает:
    - 200 OK - если счет существует
    - 404 Not Found - если счет не найден
    """
    # Аутентификация: тот же принцип, что и в /receive — отсекаем
    # анонимный перебор номеров счетов.
    if config.INTERBANK_SHARED_SECRET:
        if x_bank_auth_token != config.INTERBANK_SHARED_SECRET:
            raise HTTPException(401, "Invalid interbank auth token")
    elif not x_bank_auth_token:
        raise HTTPException(401, "Missing x-bank-auth-token")

    result = await db.execute(
        select(Account).where(Account.account_number == account_number)
    )
    account = result.scalar_one_or_none()
    
    if account:
        return {
            "exists": True,
            "account_number": account_number,
            "bank_code": config.BANK_CODE
        }
    else:
        raise HTTPException(404, f"Account {account_number} not found")


@router.get("/transfers", response_model=list)
async def list_interbank_transfers(
    db: AsyncSession = Depends(get_db),
    limit: int = 50
):
    """
    ## 📋 Список всех межбанковских переводов
    
    Для мониторинга и отладки (админ endpoint)
    """
    result = await db.execute(
        select(InterbankTransfer)
        .order_by(InterbankTransfer.created_at.desc())
        .limit(limit)
    )
    transfers = result.scalars().all()
    
    return [
        {
            "transfer_id": t.transfer_id,
            "from_bank": t.from_bank,
            "to_bank": t.to_bank,
            "amount": float(t.amount),
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None
        }
        for t in transfers
    ]

