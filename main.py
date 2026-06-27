"""
Главное FastAPI приложение банка
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

try:
    # Попытка относительного импорта (для пакетного режима)
    from .config import config
    from .database import engine, AsyncSessionLocal
    from .models import Base
    from .seed import seed_if_empty
    from .middleware import APILoggingMiddleware
    from .api import (
        accounts, auth, consents, payments, admin, products, well_known,
        banker, product_agreements, product_agreement_consents,
        product_applications, customer_leads, product_offers, product_offer_consents,
        vrp_consents, vrp_payments, interbank, payment_consents, multibank_proxy, cards
    )
except ImportError:
    # Абсолютный импорт (для прямого запуска)
    from config import config
    from database import engine, AsyncSessionLocal
    from models import Base
    from seed import seed_if_empty
    from middleware import APILoggingMiddleware
    from api import (
        accounts, auth, consents, payments, admin, products, well_known, 
        banker, product_agreements, product_agreement_consents,
        product_applications, customer_leads, product_offers, product_offer_consents,
        vrp_consents, vrp_payments, interbank, payment_consents, multibank_proxy, cards
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events"""
    # Startup
    print(f"🏦 Starting {config.BANK_NAME} ({config.BANK_CODE})")
    print(f"📍 Database: {config.DATABASE_URL.split('@')[1] if '@' in config.DATABASE_URL else 'local'}")
    
    # Create tables (в production использовать Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Засидить демо-данные, если БД пуста (идемпотентно)
    try:
        async with AsyncSessionLocal() as session:
            seeded = await seed_if_empty(session)
            if seeded:
                print(f"🌱 Seeded demo data for {config.BANK_CODE}")
    except Exception as e:
        print(f"⚠️  Seeding skipped/failed: {e}")

    yield
    
    # Shutdown
    print(f"🛑 Stopping {config.BANK_NAME}")
    await engine.dispose()


# Create FastAPI app
openapi_tags = [
    {"name": "0 Аутентификация вызывающей системы", "description": "Получите токен для работы с API"},
    {"name": "1 Согласия на доступ к счетам", "description": "Создание и управление согласиями для доступа к данным клиентов"},
    {"name": "2 Счета и балансы", "description": "Просмотр счетов, балансов и истории транзакций"},
    {"name": "3 Согласия на переводы", "description": "Согласия для совершения платежей от имени клиента"},
    {"name": "4 Переводы", "description": "Создание платежей и проверка их статуса"},
    {"name": "5 Каталог продуктов", "description": "Депозиты, кредиты, карты — каталог банковских продуктов"},
    {"name": "6 Согласия на управление договорами", "description": "Согласия на открытие/закрытие продуктов от имени клиента"},
    {"name": "7 Договоры с продуктами", "description": "Открытие и закрытие депозитов, кредитов и карт"},
    {"name": "Technical: Well-Known", "description": "JWKS — публичные ключи для проверки JWT"},
]

app = FastAPI(
    title=f"{config.BANK_NAME} API",
    description="",
    version=config.API_VERSION,
    lifespan=lifespan,
    openapi_tags=openapi_tags,
    swagger_ui_parameters={"tagsSorter": "alpha", "operationsSorter": "alpha"},
    docs_url=None  # Отключаем автоматическую генерацию /docs
)

# CORS - разрешить запросы между всеми банками
# Для мультибанковых приложений нужно разрешить cross-origin запросы
allowed_origins = [
    "http://localhost:8001",  # VBank (dev)
    "http://localhost:8002",  # ABank (dev)
    "http://localhost:8003",  # SBank (dev)
    "http://localhost",       # Прокси (dev)
    "http://localhost:3000",  # Directory (dev)
    "https://vbank.open.bankingapi.ru",  # VBank (prod)
    "https://abank.open.bankingapi.ru",  # ABank (prod)
    "https://sbank.open.bankingapi.ru",  # SBank (prod)
    "https://open.bankingapi.ru",  # HackAPI Platform
    "https://www.open.bankingapi.ru",  # HackAPI Platform (www)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # Все банки + прокси (dev + prod)
    allow_origin_regex=r"http://localhost:\d+",  # Разрешить localhost с любым портом для разработки команд
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add API logging middleware
app.add_middleware(APILoggingMiddleware)


# Кастомная страница Swagger
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    """Swagger UI"""
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{config.BANK_NAME} API - Swagger UI</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        window.onload = () => {{
            window.ui = SwaggerUIBundle({{
                url: '/openapi.json',
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                    SwaggerUIBundle.presets.apis,
                    SwaggerUIBundle.SwaggerUIStandalonePreset
                ],
                tagsSorter: 'alpha',
                operationsSorter: 'alpha'
            }});
        }};
    </script>
</body>
</html>
    """)


# Include routers
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(cards.router)
app.include_router(consents.router)
app.include_router(payment_consents.router)
app.include_router(payments.router)
app.include_router(products.router)
app.include_router(product_agreements.router)
app.include_router(product_agreement_consents.router)
app.include_router(product_applications.router)
app.include_router(customer_leads.router)
app.include_router(product_offers.router)
app.include_router(product_offer_consents.router)
app.include_router(vrp_consents.router)
app.include_router(vrp_payments.router)
app.include_router(banker.router)
app.include_router(admin.router)
app.include_router(interbank.router)
app.include_router(multibank_proxy.router)
app.include_router(well_known.router)

# Mount static files (frontend)
frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/client", StaticFiles(directory=str(frontend_path / "client"), html=True), name="client")
    app.mount("/banker", StaticFiles(directory=str(frontend_path / "banker"), html=True), name="banker")


@app.get("/", summary="Информация о банке")
async def root():
    """Root endpoint"""
    return {
        "bank": config.BANK_NAME,
        "bank_code": config.BANK_CODE,
        "api_version": config.API_VERSION,
        "status": "online"
    }


@app.get("/developer.html", response_class=HTMLResponse, include_in_schema=False)
async def developer_page():
    """
    Публичная страница регистрации команды
    
    Доступна всем без авторизации для самостоятельной регистрации команд
    """
    from pathlib import Path
    developer_file = Path(__file__).parent / "frontend" / "developer.html"
    if developer_file.exists():
        return developer_file.read_text(encoding='utf-8')
    return "<h1>404 - Developer page not found</h1>"


@app.get("/health", summary="Проверка работоспособности")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "bank": config.BANK_CODE,
        "timestamp": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    
    # Определяем порт на основе bank_code
    port_map = {
        "vbank": 8001,
        "abank": 8002,
        "sbank": 8003
    }
    port = port_map.get(config.BANK_CODE, 8001)
    
    uvicorn.run(app, host="0.0.0.0", port=port)

