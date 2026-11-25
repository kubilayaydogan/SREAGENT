"""Platform yapılandırması — yalnız ortam değişkenlerinden. Gizli değerler loglanmaz, repr'de gizlidir."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v not in (None, "") else default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class PlatformSettings:
    database_url: str = field(repr=False)          # postgresql+psycopg://user:pw@host:5432/db
    jwt_secret: str = field(repr=False)
    jwt_ttl_minutes: int = 480
    admin_username: str = "admin"
    admin_password: str = field(default="", repr=False)   # yalnız ilk açılışta admin yoksa kullanılır
    cors_origins: tuple[str, ...] = ()
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_pool_timeout_s: int = 10
    db_statement_timeout_ms: int = 30000
    max_concurrent_investigations: int = 2
    investigation_timeout_s: int = 240
    chat_history_messages: int = 12
    retention_days: int = 180
    worker_id: str = ""
    environment: str = "lab"
    watch_enabled: bool = True                    # watch durum makinesi (PG durumlu, sre-worker)
    health_check_minutes: int = 15                # zamanlanmış sağlık kontrolü aralığı (0 = kapalı)
    health_check_window_minutes: int = 15
    health_check_llm: bool = True                 # periyodik kontrolde de RCA Claude (Opus 5.5) ile yazılır
    slack_webhook_url: str = field(default="", repr=False)   # Slack Incoming Webhook; boşsa bildirim kapalı
    slack_risk_threshold: float = 75.0           # risk_score >= eşik olan tamamlanmış RCA'lar bildirilir
    public_url: str = "http://172.20.229.26:8090"   # bildirimdeki araştırma linki için arayüz adresi
    langsmith_sync_minutes: int = 5               # LangSmith → sre.llm_runs senkron aralığı (0 = kapalı)
    langsmith_backfill_days: int = 7              # ilk senkronda geriye dönük gün (LangSmith base trace ~14 gün tutar)
    # Güvenlik P0
    session_idle_minutes: int = 30                # işlem yapılmayan oturum bu süreden sonra geçersiz (0 = kapalı)
    login_max_fails: int = 5                      # pencere içinde başarısız giriş sınırı (kullanıcı adı + IP)
    login_window_s: int = 300
    run_rate_per_minute: int = 6                  # kullanıcı başına dakikada başlatılabilecek agent çalıştırması
    trusted_proxies: tuple[str, ...] = ()         # X-Forwarded-For yalnız bu eşlerden kabul edilir (CIDR)
    api_docs: bool = False                        # /api/docs ve /api/openapi.json (varsayılan kapalı)
    llm_model_allowlist: tuple[str, ...] = ()     # boş → bilinen Claude 5 modelleri + ortam modelleri (P1.6)

    @property
    def libpq_url(self) -> str:
        """LangGraph (psycopg) için SQLAlchemy sürücü öneki olmadan bağlantı dizesi."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def secrets(self) -> list[str]:
        out = [self.jwt_secret, self.admin_password, self.slack_webhook_url]
        if "@" in self.database_url and ":" in self.database_url.split("@", 1)[0]:
            out.append(self.database_url.split("@", 1)[0].rsplit(":", 1)[1])
        return [s for s in out if s and len(s) >= 6]


def load_platform_settings() -> PlatformSettings:
    url = os.getenv("SRE_DATABASE_URL", "")
    if not url:
        host, port = os.getenv("SRE_DB_HOST", "sre-postgres"), os.getenv("SRE_DB_PORT", "5432")
        user, db, pw = os.getenv("SRE_DB_USER", "sre"), os.getenv("SRE_DB_NAME", "sre"), os.getenv("SRE_DB_PASSWORD", "")
        url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"
    secret = os.getenv("SRE_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("SRE_JWT_SECRET en az 32 karakter olmalı (gizli .env dosyasında üretin)")
    return PlatformSettings(
        database_url=url,
        jwt_secret=secret,
        jwt_ttl_minutes=_int("SRE_JWT_TTL_MINUTES", 480),
        admin_username=os.getenv("SRE_ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("SRE_ADMIN_PASSWORD", ""),
        cors_origins=tuple(_list("SRE_CORS_ORIGINS")),
        db_pool_size=_int("SRE_DB_POOL_SIZE", 5),
        db_max_overflow=_int("SRE_DB_MAX_OVERFLOW", 5),
        db_pool_timeout_s=_int("SRE_DB_POOL_TIMEOUT_S", 10),
        db_statement_timeout_ms=_int("SRE_DB_STATEMENT_TIMEOUT_MS", 30000),
        max_concurrent_investigations=_int("SRE_MAX_CONCURRENT_INVESTIGATIONS", 2),
        investigation_timeout_s=_int("SRE_INVESTIGATION_TIMEOUT_S", 240),
        chat_history_messages=_int("SRE_CHAT_HISTORY_MESSAGES", 12),
        retention_days=_int("SRE_RETENTION_DAYS", 180),
        worker_id=os.getenv("SRE_WORKER_ID") or os.getenv("HOSTNAME", "worker"),
        environment=os.getenv("SRE_ENVIRONMENT", "lab"),
        watch_enabled=os.getenv("SRE_WATCH_ENABLED", "true").lower() in ("1", "true", "yes"),
        health_check_minutes=_int("SRE_HEALTH_CHECK_MINUTES", 15),
        health_check_window_minutes=_int("SRE_HEALTH_CHECK_WINDOW_MINUTES", 15),
        health_check_llm=os.getenv("SRE_HEALTH_CHECK_LLM", "true").lower() in ("1", "true", "yes"),
        slack_webhook_url=os.getenv("SRE_SLACK_WEBHOOK_URL", "").strip(),
        slack_risk_threshold=float(os.getenv("SRE_SLACK_RISK_THRESHOLD", "75")),
        public_url=os.getenv("SRE_PUBLIC_URL", "http://172.20.229.26:8090").rstrip("/"),
        langsmith_sync_minutes=_int("SRE_LANGSMITH_SYNC_MINUTES", 5),
        langsmith_backfill_days=_int("SRE_LANGSMITH_BACKFILL_DAYS", 7),
        session_idle_minutes=_int("SRE_SESSION_IDLE_MINUTES", 30),
        login_max_fails=_int("SRE_LOGIN_MAX_FAILS", 5),
        login_window_s=_int("SRE_LOGIN_WINDOW_S", 300),
        run_rate_per_minute=_int("SRE_RUN_RATE_PER_MINUTE", 6),
        trusted_proxies=tuple(_list("SRE_TRUSTED_PROXIES")),
        api_docs=os.getenv("SRE_API_DOCS", "false").lower() in ("1", "true", "yes"),
        llm_model_allowlist=tuple(_list("SRE_LLM_MODEL_ALLOWLIST")),
    )


@lru_cache(maxsize=1)
def get_settings() -> PlatformSettings:
    return load_platform_settings()
