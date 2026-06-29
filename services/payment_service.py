"""
Payment Service - логика переводов
Iteration 3 + Межбанковские переводы (реализовано)
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from decimal import Decimal
from datetime import datetime
from typing import Optional, Tuple
import uuid
import httpx
import logging

from models import Account, Payment, InterbankTransfer, BankCapital, Client, Transaction
from config import config

logger = logging.getLogger(__name__)


class PaymentService:
    """Сервис для обработки платежей"""
    
    @staticmethod
    async def initiate_payment(
        db: AsyncSession,
        from_account_number: str,
        to_account_number: str,
        amount: Decimal,
        description: str = "",
        payment_consent_id: Optional[str] = None,
        target_bank_hint: Optional[str] = None
    ) -> Tuple[Payment, Optional[InterbankTransfer]]:
        """
        Инициация платежа

        Args:
            target_bank_hint: код банка получателя (из creditorAccount.bank_code).
                Если указан — используется для прямого роутинга без перебора банков.

        Returns:
            (Payment, InterbankTransfer или None)
        """
        # Сумма перевода должна быть строго положительной.
        # Иначе отрицательная сумма развернула бы поток средств
        # (списание превратилось бы в зачисление отправителю).
        if amount is None or amount <= 0:
            raise ValueError("Amount must be positive")

        if from_account_number == to_account_number:
            raise ValueError("Source and destination accounts must differ")

        # Найти счет отправителя.
        # with_for_update() блокирует строку на время транзакции, чтобы два
        # одновременных платежа не могли списать с одного счёта дважды
        # (защита от двойного списания / гонки баланса).
        result = await db.execute(
            select(Account)
            .where(Account.account_number == from_account_number)
            .with_for_update()
        )
        from_account = result.scalar_one_or_none()

        if not from_account:
            raise ValueError("Source account not found")

        if from_account.status and from_account.status != "active":
            raise ValueError(f"Source account is not active (status: {from_account.status})")

        if from_account.balance < amount:
            raise ValueError("Insufficient funds")
        
        # Создать payment
        payment_id = f"pay-{uuid.uuid4().hex[:12]}"
        
        payment = Payment(
            payment_id=payment_id,
            payment_consent_id=payment_consent_id,
            account_id=from_account.id,
            amount=amount,
            currency="RUB",
            destination_account=to_account_number,
            description=description,
            status="AcceptedSettlementInProcess"
        )
        
        # Списать со счета отправителя
        from_account.balance -= amount
        
        # Попытаться найти получателя в своем банке
        result = await db.execute(
            select(Account).where(Account.account_number == to_account_number)
        )
        to_account = result.scalar_one_or_none()
        
        interbank_transfer = None
        
        db.add(payment)
        # Flush so the payments row exists before rows that FK-reference it
        # (interbank_transfers.payment_id -> payments.payment_id).
        await db.flush()

        if to_account:
            # Внутрибанковский перевод
            to_account.balance += amount
            payment.status = "AcceptedSettlementCompleted"
            payment.destination_bank = config.BANK_CODE
            payment.status_update_date_time = datetime.utcnow()
            
            # Создать транзакцию для отправителя (Debit - списание)
            transaction_debit = Transaction(
                account_id=from_account.id,
                transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                amount=amount,
                direction="debit",
                description=f"Перевод на счет {to_account_number}: {description}",
                transaction_date=datetime.utcnow()
            )
            db.add(transaction_debit)

            # Создать транзакцию для получателя (Credit - зачисление)
            transaction_credit = Transaction(
                account_id=to_account.id,
                transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                amount=amount,
                direction="credit",
                description=f"Перевод от счета {from_account_number}: {description}",
                transaction_date=datetime.utcnow()
            )
            db.add(transaction_credit)
            
        else:
            # ===== Межбанковский перевод (РЕАЛИЗОВАНО) =====
            
            # Определить банк получателя.
            # Приоритет — явный bank_code из creditorAccount (прямой роутинг).
            # Если не указан — перебор известных банков (детект по номеру счёта).
            target_bank = None
            hint = (target_bank_hint or "").strip().lower()
            if hint and hint != config.BANK_CODE and hint in config.known_bank_codes():
                target_bank = hint
            else:
                target_bank = await PaymentService._detect_target_bank(to_account_number)

            if not target_bank:
                # Счет не найден ни в каком банке - откат транзакции
                await db.rollback()
                raise ValueError(f"Target account {to_account_number} not found in any bank")
            
            # Создать запись межбанкового перевода
            transfer_id = f"transfer-{uuid.uuid4().hex[:12]}"
            interbank_transfer = InterbankTransfer(
                transfer_id=transfer_id,
                payment_id=payment_id,
                from_bank=config.BANK_CODE,
                to_bank=target_bank,
                amount=amount,
                status="processing"
            )
            db.add(interbank_transfer)
            
            # Создать транзакцию для отправителя (Debit - списание)
            transaction_debit = Transaction(
                account_id=from_account.id,
                transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                amount=amount,
                direction="debit",
                description=f"Межбанковский перевод в {target_bank} на счет {to_account_number}: {description}",
                transaction_date=datetime.utcnow()
            )
            db.add(transaction_debit)
            
            # Сохранить локальные изменения (списание со счета)
            await db.commit()
            
            # Вызвать API другого банка
            try:
                success = await PaymentService._send_interbank_transfer(
                    transfer_id=transfer_id,
                    to_bank=target_bank,
                    to_account_number=to_account_number,
                    amount=amount,
                    description=description
                )
                
                if success:
                    # Перевод успешен
                    payment.status = "AcceptedSettlementCompleted"
                    payment.destination_bank = target_bank
                    interbank_transfer.status = "completed"
                    interbank_transfer.completed_at = datetime.utcnow()
                    
                    # Обновить капитал банка-отправителя (-amount)
                    await PaymentService.update_bank_capital(
                        db=db,
                        amount_change=-amount,
                        reason=f"Outgoing transfer to {target_bank}: {transfer_id}"
                    )
                    
                    logger.info(f"Interbank transfer {transfer_id} completed: {config.BANK_CODE} -> {target_bank}, {amount} RUB")
                else:
                    # Перевод не удался - откат
                    payment.status = "Rejected"
                    interbank_transfer.status = "failed"
                    
                    # Вернуть деньги отправителю
                    from_account.balance += amount
                    
                    # Создать корректирующую транзакцию (возврат)
                    transaction_refund = Transaction(
                        account_id=from_account.id,
                        transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                        amount=amount,
                        direction="credit",
                        description=f"Возврат неудачного перевода в {target_bank}",
                        transaction_date=datetime.utcnow()
                    )
                    db.add(transaction_refund)
                    
                    logger.warning(f"Interbank transfer {transfer_id} failed, refunded to sender")
                    
            except Exception as e:
                # Ошибка при вызове API - откат
                logger.error(f"Interbank transfer {transfer_id} error: {str(e)}")
                payment.status = "Rejected"
                interbank_transfer.status = "failed"
                
                # Вернуть деньги отправителю
                from_account.balance += amount
                
                # Создать корректирующую транзакцию (возврат)
                transaction_refund = Transaction(
                    account_id=from_account.id,
                    transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
                    amount=amount,
                    direction="credit",
                    description=f"Возврат из-за ошибки межбанковского перевода: {str(e)}",
                    transaction_date=datetime.utcnow()
                )
                db.add(transaction_refund)
            
            payment.status_update_date_time = datetime.utcnow()
        
        await db.commit()
        await db.refresh(payment)
        
        return payment, interbank_transfer
    
    @staticmethod
    async def get_payment(
        db: AsyncSession,
        payment_id: str
    ) -> Optional[Payment]:
        """Получить статус платежа"""
        result = await db.execute(
            select(Payment).where(Payment.payment_id == payment_id)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def update_bank_capital(
        db: AsyncSession,
        amount_change: Decimal,
        reason: str = ""
    ):
        """
        Обновить капитал банка
        
        amount_change: положительное = увеличение, отрицательное = уменьшение
        """
        bank_code = config.BANK_CODE
        
        # Получить или создать запись капитала
        result = await db.execute(
            select(BankCapital).where(BankCapital.bank_code == bank_code)
        )
        capital_record = result.scalar_one_or_none()
        
        if not capital_record:
            # Создать если нет
            capital_record = BankCapital(
                bank_code=bank_code,
                capital=Decimal("3500000.00"),  # Начальный капитал
                initial_capital=Decimal("3500000.00")
            )
            db.add(capital_record)
        
        # Обновить капитал
        capital_record.capital += amount_change
        capital_record.updated_at = datetime.utcnow()
        
        await db.commit()
        
        return capital_record
    
    @staticmethod
    async def _detect_target_bank(account_number: str) -> Optional[str]:
        """
        Определить банк-получатель по номеру счета
        
        В реальности это делается через БИК (БИК включен в платежных реквизитах).
        В MVP: пытаемся найти счет во всех банках через HTTP запросы.
        
        Returns:
            Код банка (vbank/abank/sbank) или None
        """
        # Исключить свой банк из поиска
        banks = [b for b in config.known_bank_codes() if b != config.BANK_CODE]

        auth_token = config.INTERBANK_SHARED_SECRET or config.BANK_CODE

        # Проверяем каждый банк по его base URL (конфигурируется через
        # INTERBANK_BANK_URLS; по умолчанию — имена сервисов docker-сети)
        for bank_code in banks:
            try:
                bank_url = config.resolve_bank_url(bank_code)

                async with httpx.AsyncClient(timeout=config.INTERBANK_TIMEOUT) as client:
                    # Проверяем существование счета через спец-endpoint
                    response = await client.get(
                        f"{bank_url}/interbank/check-account/{account_number}",
                        headers={"x-bank-auth-token": auth_token}
                    )

                    if response.status_code == 200:
                        logger.info(f"Account {account_number} found in {bank_code}")
                        return bank_code

            except Exception as e:
                logger.debug(f"Failed to check account in {bank_code}: {str(e)}")
                continue

        return None
    
    @staticmethod
    async def _send_interbank_transfer(
        transfer_id: str,
        to_bank: str,
        to_account_number: str,
        amount: Decimal,
        description: str
    ) -> bool:
        """
        Отправить межбанковский перевод через HTTP API.

        Эндпоинт получателя /interbank/receive идемпотентен по transfer_id,
        поэтому сетевые ошибки (таймаут/обрыв) можно безопасно ретраить:
        если первый запрос на самом деле дошёл, повтор вернёт 200 и мы НЕ
        откатим перевод (иначе деньги были бы "созданы из воздуха" —
        получатель зачислил, а отправитель вернул себе).

        Returns:
            True если успешно, False если ошибка
        """
        # URL банка-получателя (конфигурируемый, по умолчанию docker-сеть)
        bank_url = config.resolve_bank_url(to_bank)

        transfer_data = {
            "transfer_id": transfer_id,
            "from_bank": config.BANK_CODE,
            "to_account_number": to_account_number,
            "amount": str(amount),
            "currency": "RUB",
            "description": description
        }

        auth_token = config.INTERBANK_SHARED_SECRET or config.BANK_CODE
        headers = {
            "x-bank-auth-token": auth_token,
            "Content-Type": "application/json"
        }

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=config.INTERBANK_TIMEOUT) as client:
                    response = await client.post(
                        f"{bank_url}/interbank/receive",
                        json=transfer_data,
                        headers=headers
                    )

                # 200 (идемпотентный повтор) и 201 (создано) — оба успех
                if response.status_code in (200, 201):
                    logger.info(f"Interbank transfer {transfer_id} sent successfully to {to_bank}")
                    return True

                # Определённый отказ получателя (4xx, кроме 408/429) — не ретраим
                if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                    logger.error(
                        f"Interbank transfer {transfer_id} rejected: "
                        f"{response.status_code} - {response.text}"
                    )
                    return False

                # 5xx/408/429 — временная ошибка, ретраим
                logger.warning(
                    f"Interbank transfer {transfer_id} attempt {attempt}/{max_attempts} "
                    f"got {response.status_code}, retrying"
                )

            except (httpx.TimeoutException, httpx.TransportError) as e:
                # Сетевая ошибка — безопасно ретраить (receive идемпотентен)
                logger.warning(
                    f"Interbank transfer {transfer_id} attempt {attempt}/{max_attempts} "
                    f"network error: {str(e)}, retrying"
                )
            except Exception as e:
                logger.error(f"Failed to send interbank transfer {transfer_id}: {str(e)}")
                return False

        logger.error(f"Interbank transfer {transfer_id} failed after {max_attempts} attempts")
        return False

