# Запуск федерации из 3 банков (vbank / abank / sbank)

Турнкей-стенд для демонстрации: 3 банка, у каждого своя БД, рабочие
межбанковские переводы и автосид демо-данных.

## Какой вариант выбрать

- **Отдельные инстансы** (как ты планировал) — оправданы, если банки реально
  на разных хостах/доменах. Минус: 3 деплоя и 3 БД руками.
- **Один хост + Traefik (рекомендую для демо)** — один `docker compose up`,
  домены задаёшь через env. Меньше всего возни, judge-friendly.

Оба варианта используют один и тот же образ; разница только в compose-файле.

---

## Вариант A. Один хост + Traefik (рекомендуется)

Домены маршрутизируются Traefik по `Host`. По умолчанию `*.localhost`
(резолвится в 127.0.0.1 в браузере), для прода подставь свои домены.

```bash
cp .env.federation.example .env     # задай домены, TEAM_CLIENT_ID, секреты
docker compose -f docker-compose.traefik.yml up --build
```

| Банк  | URL (по умолчанию)        | Swagger              |
|-------|---------------------------|----------------------|
| VBank | http://vbank.localhost    | …/docs               |
| ABank | http://abank.localhost    | …/docs               |
| SBank | http://sbank.localhost    | …/docs               |

Свои домены — просто в `.env`:

```dotenv
VBANK_DOMAIN=vbank.team218.ru
ABANK_DOMAIN=abank.team218.ru
SBANK_DOMAIN=sbank.team218.ru
```

(DNS этих доменов должен указывать на хост с Traefik; порт 80 открыт.)
Дашборд Traefik: http://localhost:8090/dashboard/

## Вариант B. Один хост, прямые порты (без Traefik)

```bash
docker compose -f docker-compose.banks.yml up --build
```

| Банк  | URL                   |
|-------|-----------------------|
| VBank | http://localhost:8001 |
| ABank | http://localhost:8002 |
| SBank | http://localhost:8003 |

---

## Сид (детерминированный, НЕ рандомный)

Настраивается через env (см. `.env.federation.example`):

| Переменная           | Назначение                                   | Дефолт   |
|----------------------|----------------------------------------------|----------|
| `TEAM_CLIENT_ID`     | код команды → person_id `team218-1, -2, …`   | team200  |
| `TEAM_CLIENT_SECRET` | секрет для `POST /auth/bank-token` и логина  | —        |
| `SEED_CLIENTS`       | сколько клиентов завести                      | 2        |
| `SEED_BALANCE`       | стартовый баланс счёта                        | 1000000  |
| `SEED_TX`            | карты + история трат по мерчантам (MCC, город)| true     |
| `SEED_TX_MONTHS`     | за сколько последних месяцев генерить историю | 3        |

При `SEED_TX=true` каждому клиенту выпускается дебетовая карта и
генерируется реалистичная история: ежемесячная зарплата + траты по
мерчантам (Пятёрочка, Лукойл, OZON…) с MCC, городом и привязкой к карте —
эндпоинт `GET /accounts/{id}/transactions` отдаёт их с детализацией.

person_id одинаковы во всех банках, номера счетов различаются банком
(7-я цифра: vbank=1, abank=2, sbank=3). При `TEAM_CLIENT_ID=team218`:

| Клиент    | VBank счёт              | ABank счёт              |
|-----------|-------------------------|-------------------------|
| team218-1 | `40817810000000000001`  | `40817820000000000001`  |
| team218-2 | `40817810000000000002`  | `40817820000000000002`  |

## Пример: межбанковский перевод

Клиент vbank переводит со своего счёта на счёт в abank. Подставь свой
`TEAM_CLIENT_SECRET` (он же пароль логина клиента команды) и хост банка.

```bash
BANK=http://vbank.localhost          # или http://localhost:8001 (вариант B)
SECRET=change-me-team-secret

TOKEN=$(curl -s $BANK/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"team218-1\",\"password\":\"$SECRET\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST $BANK/payments \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"data":{"initiation":{
    "instructedAmount":{"amount":"1000.00","currency":"RUB"},
    "debtorAccount":  {"identification":"40817810000000000001"},
    "creditorAccount":{"identification":"40817820000000000002","bank_code":"abank"},
    "comment":"demo"
  }}}'
```

Баланс `40817820000000000002` в ABank вырастет на 1000.

## Доступ извне / межбанк между разными хостами

Внутри одного compose банки ходят друг к другу по именам сервисов
(`http://vbank:8000`) — менять ничего не нужно. Если банки на РАЗНЫХ хостах,
задай каждому реальные адреса соседей:

```yaml
environment:
  INTERBANK_BANK_URLS: >-
    {"vbank":"https://vbank.team218.ru",
     "abank":"https://abank.team218.ru",
     "sbank":"https://sbank.team218.ru"}
```

`INTERBANK_SHARED_SECRET` и `SECRET_KEY` должны совпадать у всех банков.

## Сброс данных

```bash
docker compose -f docker-compose.traefik.yml down -v   # -v удаляет тома БД
```
