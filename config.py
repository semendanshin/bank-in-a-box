"""
Конфигурация банка
Команды кастомизируют эти параметры
"""
import json
from typing import Dict, List
from pydantic_settings import BaseSettings


class BankConfig(BaseSettings):
    """Настройки банка"""

    # === ИДЕНТИФИКАЦИЯ БАНКА (КАСТОМИЗИРУЙ!) ===
    BANK_CODE: str = "vbank"
    BANK_NAME: str = "Virtual Bank"
    BANK_DESCRIPTION: str = "Виртуальный банк - эмуляция от организаторов"

    # === МЕЖБАНКОВСКИЕ ПЕРЕВОДЫ (роутинг/обнаружение банков) ===
    # Список известных банков федерации (коды через запятую).
    KNOWN_BANKS: str = "vbank,abank,sbank"
    # Шаблон для разрешения base URL банка по его коду.
    # По умолчанию — имена сервисов в docker-сети организаторов.
    # Для реального деплоя переопредели через INTERBANK_BANK_URLS.
    INTERBANK_URL_TEMPLATE: str = "http://{code}:8000"
    # Опциональная JSON-карта code -> base_url, переопределяет шаблон.
    # Пример: '{"vbank":"https://vbank.open.bankingapi.ru","abank":"https://abank.open.bankingapi.ru"}'
    INTERBANK_BANK_URLS: str = ""
    # Таймаут (сек) для межбанковских HTTP-запросов.
    INTERBANK_TIMEOUT: float = 10.0
    # Опциональный общий секрет для аутентификации входящих переводов.
    # Если задан — /interbank/receive требует x-bank-auth-token == секрет.
    INTERBANK_SHARED_SECRET: str = ""
    
    # === DATABASE ===
    DATABASE_URL: str = "postgresql://hackapi_user:hackapi_pass@localhost:5432/vbank_db"
    
    # === SECURITY ===
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # === API ===
    API_VERSION: str = "2.1"
    API_BASE_PATH: str = ""
    
    # === REGISTRY (для федеративной архитектуры) ===
    REGISTRY_URL: str = "http://localhost:3000"
    PUBLIC_URL: str = "http://localhost:8001"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # tolerate federation/seed env vars (TEAM_*, SEED_*) read elsewhere

    # === Хелперы межбанковского роутинга ===

    def _bank_url_map(self) -> Dict[str, str]:
        """Распарсить JSON-карту code -> base_url (пустую при ошибке)."""
        if not self.INTERBANK_BANK_URLS:
            return {}
        try:
            data = json.loads(self.INTERBANK_BANK_URLS)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (ValueError, TypeError):
            pass
        return {}

    def resolve_bank_url(self, bank_code: str) -> str:
        """Вернуть base URL банка по коду (карта приоритетнее шаблона)."""
        mapping = self._bank_url_map()
        if bank_code in mapping:
            return mapping[bank_code].rstrip("/")
        return self.INTERBANK_URL_TEMPLATE.format(code=bank_code).rstrip("/")

    def known_bank_codes(self) -> List[str]:
        """Коды всех известных банков федерации (из KNOWN_BANKS + карты)."""
        codes = [c.strip() for c in self.KNOWN_BANKS.split(",") if c.strip()]
        for code in self._bank_url_map().keys():
            if code not in codes:
                codes.append(code)
        return codes


# Singleton instance
config = BankConfig()

