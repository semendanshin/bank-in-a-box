# Запуск федерации из 3 банков

Поднимает **vbank**, **abank**, **sbank**, каждый со своей БД, автосидингом
демо-данных и рабочими межбанковскими переводами «из коробки».

## 1. Запуск

```bash
docker compose -f docker-compose.banks.yml up --build
```

Доступ снаружи:

| Банк  | URL                     | Swagger                       |
|-------|-------------------------|-------------------------------|
| VBank | http://localhost:8001   | http://localhost:8001/docs    |
| ABank | http://localhost:8002   | http://localhost:8002/docs    |
| SBank | http://localhost:8003   | http://localhost:8003/docs    |

Схема БД создаётся приложением (`create_all`), демо-данные сидятся
автоматически при первом старте (`seed.py`) — никаких ручных SQL.

## 2. Что засеяно (в каждом банке)

- Команда `team200` / секрет `5OAaa4DYzYKfnOU6zbR34ic5qMm7VSMB`
  (для `POST /auth/bank-token`).
- Клиенты `team200-1 … team200-10` (одинаковые person_id во всех банках)
  и по одному checking-счёту у каждого.
- Авто-одобрение согласий (account + payment) — для turnkey-межбанка.

Номера счетов различаются банком (7-я цифра): vbank=`1`, abank=`2`, sbank=`3`.

| Клиент    | VBank счёт              | ABank счёт              |
|-----------|-------------------------|-------------------------|
| team200-1 | `40817810000000000001`  | `40817820000000000001`  |
| team200-2 | `40817810000000000002`  | `40817820000000000002`  |

## 3. Межбанковский перевод (пример)

Клиент vbank переводит со своего счёта на счёт в abank.

```bash
# 1) Логин клиента в VBank (пароль = секрет команды)
TOKEN=$(curl -s http://localhost:8001/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"team200-1","password":"5OAaa4DYzYKfnOU6zbR34ic5qMm7VSMB"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2) Платёж в ABank (bank_code маршрутизирует перевод напрямую)
curl -s -X POST http://localhost:8001/payments \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "data": {"initiation": {
      "instructedAmount": {"amount": "1000.00", "currency": "RUB"},
      "debtorAccount":   {"identification": "40817810000000000001"},
      "creditorAccount": {"identification": "40817820000000000002", "bank_code": "abank"},
      "comment": "Тестовый межбанк"
    }}
  }'
```

Проверить зачисление в ABank можно через его API/БД — баланс
`40817820000000000002` вырастет на 1000.

### TPP-сценарий (через согласие на платёж)

Если инициирует не сам клиент, а стороннее приложение/банк:

```bash
# токен команды в банке-плательщике
BTOKEN=$(curl -s -X POST "http://localhost:8001/auth/bank-token?client_id=team200&client_secret=5OAaa4DYzYKfnOU6zbR34ic5qMm7VSMB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# согласие на платёж (auto-approve) -> вернёт consent_id (pcon-...)
curl -s -X POST "http://localhost:8001/payment-consents/request?client_id=team200-1" \
  -H "Authorization: Bearer $BTOKEN" -H 'x-requesting-bank: team200' \
  -H 'Content-Type: application/json' \
  -d '{"data":{"initiation":{
        "instructedAmount":{"amount":"1000.00","currency":"RUB"},
        "debtorAccount":{"identification":"40817810000000000001"},
        "creditorAccount":{"identification":"40817820000000000002"}}}}'

# затем POST /payments с заголовками x-requesting-bank + x-payment-consent-id
# (debtor/creditor/сумма должны совпадать с согласием)
```

## 4. Доступ с другого хоста / в интернете

- Порты `8001/8002/8003` уже проброшены на хост — доступны по IP машины.
- Если банки живут на разных хостах, задай каждому реальные адреса соседей:

  ```yaml
  environment:
    INTERBANK_BANK_URLS: >-
      {"vbank":"https://vbank.example.com",
       "abank":"https://abank.example.com",
       "sbank":"https://sbank.example.com"}
  ```

- `INTERBANK_SHARED_SECRET` должен совпадать у всех банков федерации —
  он проверяется на входящих `/interbank/receive` и `/interbank/check-account`.

## 5. Сброс данных

```bash
docker compose -f docker-compose.banks.yml down -v   # -v удаляет тома БД
```
