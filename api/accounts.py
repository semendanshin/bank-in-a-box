"""
Accounts API - Счета и балансы
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Query, Path
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel
from decimal import Decimal
import uuid

from database import get_db
from models import Account, Client, Transaction, BankCapital, Merchant, Card
from services.auth_service import require_any_token, require_client, caller_owns_client
from services.consent_service import ConsentService
from sqlalchemy.orm import selectinload


async def _authorize_account_access(db, token_data, account, x_requesting_bank, x_consent_id, permission):
    """
    Авторизовать доступ к данным счёта:
    - владелец (свой client-токен или команда к своему клиенту) — разрешено;
    - чужой клиент — только межбанк через действующее согласие;
    - иначе 403.
    """
    owner = (await db.execute(select(Client).where(Client.id == account.client_id))).scalar_one_or_none()
    owner_pid = owner.person_id if owner else None

    if caller_owns_client(token_data, owner_pid):
        return

    if x_requesting_bank:
        consent = await ConsentService.check_consent(
            db=db,
            client_person_id=owner_pid,
            requesting_bank=x_requesting_bank,
            permissions=[permission],
            consent_id=x_consent_id,
        )
        if consent:
            return
        raise HTTPException(403, {
            "error": "CONSENT_REQUIRED",
            "message": "Требуется действующее согласие клиента",
        })

    raise HTTPException(403, "Access denied")


router = APIRouter(prefix="/accounts", tags=["2 Счета и балансы"])


@router.get("", summary="1. Получить список счетов")
async def get_accounts(
    client_id: Optional[str] = Query(None, example="team200-1", description="ID клиента (например team200-1). Обязателен для межбанковых запросов"),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", example="consent-69e75facabba", description="ID согласия (получите через POST /account-consents/request). Обязателен для межбанковых запросов"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", example="team200", description="ID вашей команды (от организаторов). Укажите для запроса данных из другого банка"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    ## 💳 Получение списка счетов клиента
    
    ### Два режима работы:
    
    #### 1️⃣ Запрос своих счетов (в том же банке)
    ```bash
    GET /accounts
    Headers:
      Authorization: Bearer <client_token>
    ```
    
    #### 2️⃣ Межбанковый запрос (с согласием)
    ```bash
    GET /accounts?client_id=cli-ab-001
    Headers:
      Authorization: Bearer <bank_token>
      X-Requesting-Bank: team200
      X-Consent-Id: <consent_id>
    ```
    
    ### Ответ содержит:
    - `account_id` — уникальный идентификатор счета
    - `currency` — валюта (RUB, USD, EUR)
    - `account_type` — тип счета (Personal, Business)
    - `nickname` — название счета
    - `servicer` — информация о банке
    
    ### ⚠️ Важно для межбанковых запросов:
    1. Сначала создайте согласие: `POST /account-consents/request`
    2. Клиент должен одобрить согласие в банке-владельце счетов
    3. Используйте полученный `consent_id` в заголовке `X-Consent-Id`
    4. Укажите свой банк в `X-Requesting-Bank`
    
    ### Примечание:
    - Без согласия межбанковый запрос вернет 403 с подсказкой, как получить согласие
    - Согласие имеет срок действия (обычно 90 дней)
    """
    # Определяем, чей это запрос
    if x_requesting_bank:
        # Межбанковский запрос - требуется согласие
        if not client_id:
            raise HTTPException(400, "client_id required for interbank requests")
        
        # Проверить согласие
        consent = await ConsentService.check_consent(
            db=db,
            client_person_id=client_id,
            requesting_bank=x_requesting_bank,
            permissions=["ReadAccountsDetail"],
            consent_id=x_consent_id
        )
        
        if not consent:
            raise HTTPException(
                403,
                detail={
                    "error": "CONSENT_REQUIRED",
                    "message": "Требуется согласие клиента",
                    "consent_request_url": f"/account-consents/request"
                }
            )
        
        target_client_id = client_id
        
    else:
        # Запрос собственного клиента - требуется client токен
        if token_data.get("type") != "client":
            raise HTTPException(401, "Client token required for own account access")
        target_client_id = token_data["client_id"]
    
    # Получаем клиента для имени
    client_result = await db.execute(
        select(Client).where(Client.person_id == target_client_id)
    )
    client = client_result.scalar_one_or_none()
    client_name = client.full_name if client else ""
    
    # Получаем счета
    result = await db.execute(
        select(Account)
        .join(Client)
        .where(Client.person_id == target_client_id)
        .where(Account.status == "active")
    )
    accounts = result.scalars().all()
    
    # Формируем ответ
    return {
        "data": {
            "account": [
                {
                    "accountId": f"acc-{acc.id}",
                    "status": "Enabled" if acc.status == "active" else "Disabled",
                    "currency": acc.currency,
                    "accountType": "Personal" if acc.account_type == "checking" else "Business",
                    "accountSubType": acc.account_type.title(),
                    "nickname": f"{acc.account_type.title()} счет",
                    "openingDate": acc.opened_at.date().isoformat(),
                    "account": [
                        {
                            "schemeName": "RU.CBR.PAN",
                            "identification": acc.account_number,
                            "name": client_name
                        }
                    ]
                }
                for acc in accounts
            ]
        },
        "links": {
            "self": "/accounts"
        },
        "meta": {
            "totalPages": 1
        }
    }


@router.get("/{account_id}", summary="2. Получить детали счета")
async def get_account(
    account_id: str = Path(..., example="acc-1010", description="ID счета"),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", example="consent-69e75facabba", description="ID согласия (получите через POST /account-consents/request). Обязателен для межбанковых запросов"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", example="team200", description="ID вашей команды (от организаторов). Укажите для запроса данных из другого банка"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Получение детальной информации о счете
    
    **Требует:** Client token (для своих счетов) или Bank token с согласием (межбанк)
    """
    # Извлекаем ID из строки "acc-123"
    acc_id = int(account_id.replace("acc-", ""))

    result = await db.execute(select(Account).where(Account.id == acc_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    await _authorize_account_access(
        db, token_data, account, x_requesting_bank, x_consent_id, "ReadAccountsDetail")

    return {
        "data": {
            "account": [
                {
                    "accountId": f"acc-{account.id}",
                    "status": "Enabled",
                    "currency": account.currency,
                    "accountType": "Personal",
                    "accountSubType": account.account_type.title(),
                    "description": f"{account.account_type} account",
                    "nickname": f"{account.account_type.title()} счет",
                    "openingDate": account.opened_at.date().isoformat()
                }
            ]
        }
    }


@router.get("/{account_id}/balances", summary="3. Получить баланс счета")
async def get_balances(
    account_id: str = Path(..., example="acc-1010", description="ID счета"),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", example="consent-69e75facabba", description="ID согласия (получите через POST /account-consents/request). Обязателен для межбанковых запросов"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", example="team200", description="ID вашей команды (от организаторов). Укажите для запроса данных из другого банка"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Получение баланса счета
    
    **Требует:** Client token (для своих счетов) или Bank token с согласием (межбанк)
    """
    acc_id = int(account_id.replace("acc-", ""))

    result = await db.execute(
        select(Account).where(Account.id == acc_id)
    )
    account = result.scalar_one_or_none()

    if not account:
        raise HTTPException(404, "Account not found")

    await _authorize_account_access(
        db, token_data, account, x_requesting_bank, x_consent_id, "ReadBalances")

    return {
        "data": {
            "balance": [
                {
                    "accountId": f"acc-{account.id}",
                    "type": "InterimAvailable",
                    "dateTime": datetime.utcnow().isoformat() + "Z",
                    "amount": {
                        "amount": str(account.balance),
                        "currency": account.currency
                    },
                    "creditDebitIndicator": "Credit"
                },
                {
                    "accountId": f"acc-{account.id}",
                    "type": "InterimBooked",
                    "dateTime": datetime.utcnow().isoformat() + "Z",
                    "amount": {
                        "amount": str(account.balance),
                        "currency": account.currency
                    },
                    "creditDebitIndicator": "Credit"
                }
            ]
        }
    }


@router.get("/{account_id}/transactions", summary="4. Получить историю транзакций")
async def get_transactions(
    account_id: str = Path(..., example="acc-1010", description="ID счета"),
    from_booking_date_time: Optional[str] = Query(None, example="2025-01-01T00:00:00Z"),
    to_booking_date_time: Optional[str] = Query(None, example="2025-12-31T23:59:59Z"),
    page: int = Query(1, example=1),
    limit: int = Query(50, ge=1, le=100, example=50),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", example="consent-69e75facabba", description="ID согласия (получите через POST /account-consents/request). Обязателен для межбанковых запросов"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", example="team200", description="ID вашей команды (от организаторов). Укажите для запроса данных из другого банка"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Получение списка транзакций по счету
    
    **Пагинация:**
    - `page` — номер страницы (по умолчанию: 1)
    - `limit` — количество транзакций на странице (по умолчанию: 50, макс: 500)
    
    **Примеры:**
    - `GET /accounts/acc-1/transactions` — первые 50 транзакций
    - `GET /accounts/acc-1/transactions?page=2&limit=100` — следующие 100 транзакций
    - `GET /accounts/acc-1/transactions?limit=200` — первые 200 транзакций
    """
    acc_id = int(account_id.replace("acc-", ""))

    acc_result = await db.execute(select(Account).where(Account.id == acc_id))
    account = acc_result.scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")

    await _authorize_account_access(
        db, token_data, account, x_requesting_bank, x_consent_id, "ReadTransactionsDetail")

    # Валидация параметров
    if page < 1:
        page = 1
    if limit < 1:
        limit = 50
    if limit > 500:
        limit = 500
    
    # Offset для пагинации
    offset = (page - 1) * limit
    
    query = select(Transaction).where(Transaction.account_id == acc_id)
    
    # Фильтры по датам (опционально)
    if from_booking_date_time:
        # TODO: parse date
        pass
    
    # Подсчет общего количества
    from sqlalchemy import func
    count_query = select(func.count()).select_from(Transaction).where(Transaction.account_id == acc_id)
    total_result = await db.execute(count_query)
    total_count = total_result.scalar()
    
    # Получение транзакций с пагинацией + загрузка связанных merchant и card
    result = await db.execute(
        query
        .options(
            selectinload(Transaction.merchant),
            selectinload(Transaction.card)
        )
        .order_by(Transaction.transaction_date.desc())
        .limit(limit)
        .offset(offset)
    )
    transactions = result.scalars().all()
    
    # Формирование ссылок для пагинации
    base_url = f"/accounts/{account_id}/transactions"
    links = {
        "self": f"{base_url}?page={page}&limit={limit}"
    }
    
    # Добавляем ссылку на следующую страницу если есть еще транзакции
    if offset + limit < total_count:
        links["next"] = f"{base_url}?page={page + 1}&limit={limit}"
    
    # Добавляем ссылку на предыдущую страницу если не первая страница
    if page > 1:
        links["prev"] = f"{base_url}?page={page - 1}&limit={limit}"
    
    return {
        "data": {
            "transaction": [
                {
                    "accountId": f"acc-{acc_id}",
                    "transactionId": tx.transaction_id,
                    "amount": {
                        "amount": str(abs(tx.amount)),
                        "currency": tx.currency or "RUB"
                    },
                    "creditDebitIndicator": "Credit" if tx.direction == "credit" else "Debit",
                    "status": tx.status or "Booked",
                    "bookingDateTime": tx.transaction_date.isoformat() + "Z",
                    "valueDateTime": tx.transaction_date.isoformat() + "Z",
                    "transactionInformation": tx.description or "",
                    "bankTransactionCode": {
                        "code": tx.bank_transaction_code or ("ReceivedCreditTransfer" if tx.direction == "credit" else "IssuedDebitTransfer")
                    },
                    
                    # === НОВЫЕ ПОЛЯ: Мерчант и MCC код ===
                    "merchant": {
                        "merchantId": tx.merchant.merchant_id,
                        "name": tx.merchant.name,
                        "mccCode": tx.merchant.mcc_code,
                        "category": tx.merchant.category,
                        "city": tx.merchant.city,
                        "country": tx.merchant.country,
                        "address": tx.merchant.address
                    } if tx.merchant else None,
                    
                    # === География транзакции ===
                    "transactionLocation": {
                        "city": tx.transaction_city,
                        "country": tx.transaction_country
                    } if tx.transaction_city or tx.transaction_country else None,
                    
                    # === Информация о карте ===
                    "card": {
                        "cardId": tx.card.card_id,
                        "cardNumber": "****" + tx.card.card_number[-4:],
                        "cardType": tx.card.card_type,
                        "cardName": tx.card.card_name
                    } if tx.card else None,
                    
                    # === Устаревшие поля (для обратной совместимости) ===
                    "counterparty": tx.counterparty
                }
                for tx in transactions
            ]
        },
        "links": links,
        "meta": {
            "totalPages": (total_count + limit - 1) // limit,
            "totalRecords": total_count,
            "currentPage": page,
            "pageSize": limit
        }
    }


class CreateAccountRequest(BaseModel):
    """Запрос на создание нового счета"""
    account_type: str
    initial_balance: float = 0


class AccountStatusUpdate(BaseModel):
    """Обновление статуса счета"""
    status: str


class AccountCloseRequest(BaseModel):
    """Запрос на закрытие счета с переводом остатка"""
    action: str  # "transfer" или "donate"
    destination_account_id: Optional[str] = None  # Для action=transfer


@router.post("", summary="5. Создать счет")
async def create_account(
    request: CreateAccountRequest,
    client_id: Optional[str] = Query(None, description="ID клиента (обязательно для bank_token)", example="team200-1"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", description="ID запрашивающего банка"),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", description="ID согласия"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Создание нового счета
    
    Поддерживаемые типы: checking, savings
    
    ### 🔑 Аутентификация:
    - **client_token**: Клиент создает счет САМОСТОЯТЕЛЬНО - согласие НЕ требуется
    - **bank_token**: Другой банк создает счет ОТ ИМЕНИ клиента - ТРЕБУЕТСЯ согласие!
    
    ### 🔐 Требования для межбанкового создания счета:
    При использовании `bank_token` обязательно:
    1. **Query parameter:** `client_id` - ID клиента
    2. **Header:** `X-Requesting-Bank` - ваш bank_code
    3. **Header:** `X-Consent-Id` - ID активного согласия
    4. **Согласие должно иметь permission:** `ManageAccounts`
    
    ### Получение согласия:
    ```bash
    POST /account-consents
    {
      "data": {
        "permissions": ["ManageAccounts"],
        "expirationDateTime": "2025-12-31T23:59:59Z"
      }
    }
    ```
    
    Клиент должен одобрить согласие в своем банке.
    """
    # Определить client_id (либо из токена, либо из параметра для bank_token)
    target_client_id = None
    is_self_operation = False  # Клиент создает счет сам
    
    if token_data.get("type") == "client":
        # Клиент создает счет САМОСТОЯТЕЛЬНО (своим client_token)
        target_client_id = token_data.get("client_id")
        is_self_operation = True
    elif client_id:
        # Другой банк создает счет ОТ ИМЕНИ клиента (bank_token + client_id)
        target_client_id = client_id
        is_self_operation = False
    else:
        raise HTTPException(401, "Unauthorized. Укажите client_id или используйте client_token")
    
    # Если это НЕ самостоятельная операция (bank_token), проверить согласие
    if not is_self_operation:
        # Проверить согласие с permissions: ["ManageAccounts"] или ["CreateAccounts"]
        consent = await ConsentService.check_consent(
            db=db,
            client_person_id=target_client_id,
            requesting_bank=x_requesting_bank or "unknown",
            permissions=["ManageAccounts"],  # или CreateAccounts
            consent_id=x_consent_id
        )
        
        if not consent:
            raise HTTPException(
                403, 
                "Forbidden. Для создания счета от имени клиента требуется активное согласие с разрешением 'ManageAccounts'. "
                "Получите согласие клиента через POST /account-consents с permissions=['ManageAccounts']."
            )
    
    # Найти клиента
    result = await db.execute(
        select(Client).where(Client.person_id == target_client_id)
    )
    client = result.scalar_one_or_none()
    
    if not client:
        raise HTTPException(404, "Client not found")
    
    # Валидация типа счета
    valid_types = ["checking", "savings"]
    if request.account_type not in valid_types:
        raise HTTPException(400, f"Invalid account type. Must be one of: {', '.join(valid_types)}")
    
    # Генерация номера счета
    # 408 - текущий счет, 42301 - сберегательный
    if request.account_type == "checking":
        account_prefix = "408"
    elif request.account_type == "savings":
        account_prefix = "42301"
    else:
        account_prefix = "408"
    
    account_number = f"{account_prefix}{uuid.uuid4().hex[:15]}"
    
    # Создать счет
    new_account = Account(
        client_id=client.id,
        account_number=account_number,
        account_type=request.account_type,
        balance=Decimal(str(request.initial_balance)),
        currency="RUB",
        status="active"
    )
    
    db.add(new_account)
    await db.commit()
    await db.refresh(new_account)
    
    # Если начальный баланс > 0, создать транзакцию
    if request.initial_balance > 0:
        initial_tx = Transaction(
            account_id=new_account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=Decimal(str(request.initial_balance)),
            direction="credit",
            counterparty="Начальное пополнение",
            description="Начальный баланс при открытии счета"
        )
        db.add(initial_tx)
        await db.commit()
    
    return {
        "data": {
            "accountId": f"acc-{new_account.id}",
            "account_number": new_account.account_number,
            "account_type": new_account.account_type,
            "balance": float(new_account.balance),
            "status": new_account.status
        },
        "meta": {
            "message": "Account created successfully"
        }
    }


@router.put("/{account_id}/status", summary="6. Изменить статус счета")
async def update_account_status(
    account_id: str,
    request: AccountStatusUpdate,
    client_id: Optional[str] = Query(None, description="ID клиента (обязательно для bank_token)", example="team200-1"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Изменение статуса счета (закрытие)
    
    Допустимые статусы: active, closed
    
    ### 🔑 Аутентификация:
    - **client_token**: `client_id` определится автоматически
    - **bank_token**: укажите `client_id` в query параметре
    """
    # Извлечь ID
    acc_id = int(account_id.replace("acc-", ""))

    # Найти счет
    result = await db.execute(
        select(Account, Client)
        .join(Client, Account.client_id == Client.id)
        .where(Account.id == acc_id)
    )
    account_data = result.first()

    if not account_data:
        raise HTTPException(404, "Account not found")

    account, client = account_data

    # Счёт должен принадлежать вызывающему (свой client-токен либо команда
    # к своему клиенту). Чужими счетами через этот endpoint управлять нельзя.
    if not caller_owns_client(token_data, client.person_id):
        raise HTTPException(403, "Access denied")
    
    # Проверить валидность статуса
    valid_statuses = ["active", "closed"]
    if request.status not in valid_statuses:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(valid_statuses)}")
    
    # Обновить статус
    account.status = request.status
    await db.commit()
    
    return {
        "data": {
            "accountId": f"acc-{account.id}",
            "account_number": account.account_number,
            "status": account.status
        },
        "meta": {
            "message": f"Account status updated to {request.status}"
        }
    }


@router.put("/{account_id}/close", summary="7. Закрыть счет с остатком")
async def close_account_with_balance(
    account_id: str,
    request: AccountCloseRequest,
    client_id: Optional[str] = Query(None, description="ID клиента (обязательно для bank_token)", example="team200-1"),
    x_requesting_bank: Optional[str] = Header(None, alias="x-requesting-bank", description="ID запрашивающего банка"),
    x_consent_id: Optional[str] = Header(None, alias="x-consent-id", description="ID согласия"),
    token_data: dict = Depends(require_any_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Закрытие счета с переводом остатка или дарением банку
    
    Actions:
    - transfer: перевести остаток на другой счет
    - donate: подарить остаток банку (увеличить capital)
    
    ### 🔑 Аутентификация:
    - **client_token**: Клиент закрывает счет САМОСТОЯТЕЛЬНО - согласие НЕ требуется
    - **bank_token**: Другой банк закрывает счет ОТ ИМЕНИ клиента - ТРЕБУЕТСЯ согласие!
    
    ### 🔐 Требования для межбанкового закрытия счета:
    При использовании `bank_token` обязательно:
    1. **Query parameter:** `client_id` - ID клиента
    2. **Header:** `X-Requesting-Bank` - ваш bank_code
    3. **Header:** `X-Consent-Id` - ID активного согласия
    4. **Согласие должно иметь permission:** `ManageAccounts`
    """
    # Определить client_id (либо из токена, либо из параметра для bank_token)
    target_client_id = None
    is_self_operation = False
    
    if token_data.get("type") == "client":
        # Клиент закрывает счет САМОСТОЯТЕЛЬНО
        target_client_id = token_data.get("client_id")
        is_self_operation = True
    elif client_id:
        # Другой банк закрывает счет ОТ ИМЕНИ клиента
        target_client_id = client_id
        is_self_operation = False
    else:
        raise HTTPException(401, "Unauthorized. Укажите client_id или используйте client_token")
    
    # Если это НЕ самостоятельная операция (bank_token), проверить согласие
    if not is_self_operation:
        consent = await ConsentService.check_consent(
            db=db,
            client_person_id=target_client_id,
            requesting_bank=x_requesting_bank or "unknown",
            permissions=["ManageAccounts"],
            consent_id=x_consent_id
        )
        
        if not consent:
            raise HTTPException(
                403, 
                "Forbidden. Для закрытия счета от имени клиента требуется активное согласие с разрешением 'ManageAccounts'. "
                "Получите согласие клиента через POST /account-consents с permissions=['ManageAccounts']."
            )
    
    # Извлечь ID
    acc_id = int(account_id.replace("acc-", ""))
    
    # Найти счет
    result = await db.execute(
        select(Account, Client)
        .join(Client, Account.client_id == Client.id)
        .where(Account.id == acc_id)
    )
    account_data = result.first()
    
    if not account_data:
        raise HTTPException(404, "Account not found")
    
    account, client = account_data
    
    # Проверить что это счет текущего клиента
    if client.person_id != target_client_id:
        raise HTTPException(403, "Access denied")
    
    balance = account.balance
    
    if request.action == "transfer":
        # Перевести остаток на другой счет
        if not request.destination_account_id:
            raise HTTPException(400, "destination_account_id required for transfer action")
        
        dest_acc_id = int(request.destination_account_id.replace("acc-", ""))
        dest_result = await db.execute(
            select(Account).where(Account.id == dest_acc_id, Account.client_id == client.id)
        )
        dest_account = dest_result.scalar_one_or_none()
        
        if not dest_account:
            raise HTTPException(404, "Destination account not found")
        
        # Перевести средства
        dest_account.balance += balance
        account.balance = Decimal("0")
        
        # Создать транзакции
        debit_tx = Transaction(
            account_id=account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=balance,
            direction="debit",
            counterparty="Закрытие счета",
            description=f"Перевод на {dest_account.account_number} при закрытии"
        )
        db.add(debit_tx)
        
        credit_tx = Transaction(
            account_id=dest_account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=balance,
            direction="credit",
            counterparty="Пополнение",
            description=f"Перевод с {account.account_number} (закрытие счета)"
        )
        db.add(credit_tx)
        
    elif request.action == "donate":
        # Подарить банку (увеличить capital)
        from config import config
        
        capital_result = await db.execute(
            select(BankCapital).where(BankCapital.bank_code == config.BANK_CODE)
        )
        capital = capital_result.scalar_one_or_none()
        
        if capital:
            capital.capital += balance
        
        # Создать транзакцию списания
        donate_tx = Transaction(
            account_id=account.id,
            transaction_id=f"tx-{uuid.uuid4().hex[:12]}",
            amount=balance,
            direction="debit",
            counterparty="Дар банку",
            description="Дарение средств банку при закрытии счета"
        )
        db.add(donate_tx)
        
        account.balance = Decimal("0")
    
    else:
        raise HTTPException(400, f"Invalid action: {request.action}")
    
    # Закрыть счет
    account.status = "closed"
    await db.commit()
    
    return {
        "data": {
            "accountId": f"acc-{account.id}",
            "account_number": account.account_number,
            "status": "closed",
            "action": request.action,
            "amount_transferred": float(balance)
        },
        "meta": {
            "message": f"Account closed with {request.action} action"
        }
    }

