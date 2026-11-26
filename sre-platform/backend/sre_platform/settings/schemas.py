"""Settings bölümlerinin tipli şemaları ve alan meta verisi.

Her bölüm bir pydantic modelidir (extra alan yasak, sınırlar sunucuda doğrulanır). Alan meta verisi:
  env      : değeri ilk açılışta belirleyen ortam değişkeni (kaynak "env" gösterimi için)
  apply    : "next_run" (sonraki koşu/tur), "next_tick" (sonraki zamanlayıcı turu), "restart" (süreç yeniden başlatılmalı)
  help     : kısa açıklama (arayüzde gösterilir)

Etkili değer önceliği: deployment kilidi (SRE_SETTINGS_LOCKS) > yönetici ayarı > ortam değişkeni > uygulama varsayılanı.
Secret değerleri bu şemalarda YOKTUR; yalnız secret referansları (env:AD | secret:ad) tutulur.
"""
from __future__ import annotations

import math
import re
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SECRET_REF_RE = re.compile(r"^(env:[A-Z][A-Z0-9_]{1,63}|secret:[a-z0-9][a-z0-9_.-]{0,63})$")


def _meta(env: str | None = None, apply: str = "next_run", help: str = "", unit: str | None = None) -> dict:
    return {"env": env, "apply": apply, "help": help, "unit": unit}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ------------------------------------------------------------------------------------------------ general
DATE_FORMATS = ("dd.MM.yyyy HH:mm", "yyyy-MM-dd HH:mm")


class GeneralSettings(_Section):
    display_name: str = Field("airlab SRE", min_length=1, max_length=48,
                              json_schema_extra=_meta(help="Menü ve sekme başlığında görünen platform adı"))
    timezone: str = Field("Europe/Istanbul", json_schema_extra=_meta(
        help="Arayüzdeki zaman gösterimi için IANA timezone. 'browser' = tarayıcının yerel saati. Kayıtlar UTC tutulur"))
    language: Literal["tr"] = Field("tr", json_schema_extra=_meta(
        help="Arayüz dili. Şu an yalnız Türkçe arayüz var; teknik terimler İngilizce kalır"))
    date_format: Literal[DATE_FORMATS] = Field("dd.MM.yyyy HH:mm", json_schema_extra=_meta(help="Tarih/saat biçimi"))

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if v != "browser" and v not in available_timezones():
            raise ValueError(f"geçersiz IANA timezone: {v}")
        return v


# ------------------------------------------------------------------------------------------------ agent
class AgentSettings(_Section):
    enabled: bool = Field(True, json_schema_extra=_meta(
        help="Kapalıyken YENİ investigation başlatılmaz (manuel, zamanlanmış, watch). Süren koşular durdurulmaz"))
    execution_mode: Literal["manual", "scheduled", "hybrid"] = Field("hybrid", json_schema_extra=_meta(
        help="manual: yalnız kullanıcı başlatır · scheduled: yalnız zamanlanmış health check ve watch · hybrid: ikisi"))
    incident_triggered: bool = Field(True, json_schema_extra=_meta(
        help="Watch bir koşul açtığında otomatik investigation çalıştır (incident yine açılır)"))
    analysis_window_minutes: int = Field(15, ge=5, le=180, json_schema_extra=_meta(
        help="Soruda süre belirtilmediğinde analiz penceresi", unit="dk"))
    baseline_minutes: int = Field(15, ge=5, le=180, json_schema_extra=_meta(
        help="Pencereden hemen önceki baseline uzunluğu", unit="dk"))
    max_window_minutes: int = Field(180, ge=30, le=720, json_schema_extra=_meta(
        help="Kullanıcının isteyebileceği en uzun analiz penceresi", unit="dk"))
    run_timeout_s: int = Field(240, ge=30, le=1800, json_schema_extra=_meta(
        env="SRE_INVESTIGATION_TIMEOUT_S", help="Investigation genel Timeout; aşılınca koşu iptal edilir", unit="sn"))
    max_concurrent_runs: int = Field(2, ge=1, le=8, json_schema_extra=_meta(
        env="SRE_MAX_CONCURRENT_INVESTIGATIONS", apply="restart",
        help="Süreç başına aynı anda çalışan investigation sayısı (backend ve worker ayrı sayar)"))
    max_extra_iterations: int = Field(2, ge=0, le=3, json_schema_extra=_meta(
        help="extra_evidence adımının en fazla tur sayısı (0 = ek evidence toplanmaz)"))
    recursion_limit: int = Field(25, ge=10, le=100, json_schema_extra=_meta(
        help="LangGraph recursion_limit (plan → collect → analyze → extra_evidence → explain → finalize)"))
    max_queries_per_run: int = Field(40, ge=5, le=150, json_schema_extra=_meta(
        help="Koşu başına MCP query (tool call) bütçesi; dolunca kalan query'ler SKIPPED_BUDGET olur"))
    query_timeout_s: int = Field(60, ge=5, le=300, json_schema_extra=_meta(help="Tek MCP query Timeout", unit="sn"))
    query_retries: int = Field(2, ge=0, le=5, json_schema_extra=_meta(
        help="Başarısız query için tekrar sayısı. Bekleme sınırlı üstel: 1, 2, 4, 8 sn (en fazla 8 sn)"))
    llm_enabled: bool = Field(True, json_schema_extra=_meta(
        help="LLM reasoning (soru yorumlama, açıklama, sohbet cevabı). Kapalıyken analiz ve risk score deterministik "
             "olarak aynen üretilir; açıklama kural tabanlıdır"))
    llm_max_calls_per_run: int = Field(12, ge=1, le=60, json_schema_extra=_meta(
        help="Koşu (sohbet turu / investigation) başına en fazla LLM çağrısı; dolunca kalan adımlar deterministik "
             "yedeğe düşer ve audit'e yazılır"))
    llm_max_tokens_per_run: int = Field(400_000, ge=10_000, le=4_000_000, json_schema_extra=_meta(
        help="Koşu başına LLM token (girdi + çıktı) sınırı; çağrıdan önce denetlenir", unit="token"))
    scheduled_llm: bool = Field(True, json_schema_extra=_meta(
        env="SRE_HEALTH_CHECK_LLM", help="Zamanlanmış health check'te de LLM açıklaması yazılsın"))
    incident_llm: bool = Field(True, json_schema_extra=_meta(
        help="Watch incident investigation'ında LLM açıklaması yazılsın"))
    watch_confirm_windows: int = Field(3, ge=1, le=10, json_schema_extra=_meta(
        apply="restart", help="Incident açmak için art arda gereken ihlal penceresi (1 dk)"))
    watch_cooldown_minutes: int = Field(10, ge=0, le=240, json_schema_extra=_meta(
        apply="restart", help="Kapanan watch koşulunun yeniden açılmadan önce beklediği süre (dedup)", unit="dk"))

    @model_validator(mode="after")
    def _windows(self):
        if self.analysis_window_minutes > self.max_window_minutes:
            raise ValueError("analysis_window_minutes, max_window_minutes'tan büyük olamaz")
        return self


# ------------------------------------------------------------------------------------------------ scheduling
class SchedulingSettings(_Section):
    health_check_enabled: bool = Field(True, json_schema_extra=_meta(
        env="SRE_HEALTH_CHECK_MINUTES", apply="next_tick", help="Zamanlanmış health check (tam analiz)"))
    health_check_interval_minutes: int = Field(15, ge=5, le=1440, json_schema_extra=_meta(
        env="SRE_HEALTH_CHECK_MINUTES", apply="next_tick", help="Health check aralığı", unit="dk"))
    health_check_window_minutes: int = Field(15, ge=5, le=180, json_schema_extra=_meta(
        env="SRE_HEALTH_CHECK_WINDOW_MINUTES", apply="next_tick", help="Her health check'in analiz penceresi", unit="dk"))
    watch_enabled: bool = Field(True, json_schema_extra=_meta(
        env="SRE_WATCH_ENABLED", apply="next_tick", help="Watch durum makinesi (1 dk pencereler, incident açar)"))
    watch_poll_seconds: int = Field(60, ge=30, le=600, json_schema_extra=_meta(
        apply="next_tick", help="Watch kontrol aralığı", unit="sn"))
    langsmith_sync_enabled: bool = Field(True, json_schema_extra=_meta(
        env="SRE_LANGSMITH_SYNC_MINUTES", apply="next_tick", help="LangSmith → LLM Monitoring özet senkronu"))
    langsmith_sync_minutes: int = Field(5, ge=1, le=120, json_schema_extra=_meta(
        env="SRE_LANGSMITH_SYNC_MINUTES", apply="next_tick", help="Senkron aralığı", unit="dk"))


# ------------------------------------------------------------------------------------------------ risk
RISK_COMPONENTS = ("latency", "errors", "saturation", "lock", "resource")
RAMPS = {"latency_ratio": (1.0, 50.0), "error_rate": (0.0, 1.0), "pool_ratio": (0.0, 1.0),
         "lock_waiters": (0.0, 100.0), "cpu_pct": (0.0, 100.0)}


class RiskSettings(_Section):
    weights: dict[str, float] = Field(
        default_factory=lambda: {"latency": 0.30, "errors": 0.25, "saturation": 0.20, "lock": 0.15, "resource": 0.10},
        json_schema_extra=_meta(help="Bileşen ağırlıkları (normalize; toplam 1.00). Eksik bileşen hesaba girmez"))
    ramps: dict[str, list[float]] = Field(
        default_factory=lambda: {"latency_ratio": [1.2, 3.0], "error_rate": [0.005, 0.05], "pool_ratio": [0.75, 1.0],
                                 "lock_waiters": [0.0, 3.0], "cpu_pct": [70.0, 95.0]},
        json_schema_extra=_meta(help="Sinyal eşikleri: [değer_sıfır, değer_bir] arası doğrusal 0..1"))
    bands: dict[str, float] = Field(default_factory=lambda: {"low": 25, "medium": 50, "high": 75},
                                    json_schema_extra=_meta(help="Severity bantları (0–100): düşük < orta < yüksek < kritik"))
    min_score_coverage: float = Field(0.6, ge=0.3, le=1.0, json_schema_extra=_meta(
        help="Missing-data politikası: ölçülebilen ağırlık bu oranın altındaysa risk score hesaplanmaz (null), "
             "0 kabul EDİLMEZ"))

    @field_validator("weights")
    @classmethod
    def _w(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != set(RISK_COMPONENTS):
            raise ValueError(f"ağırlıklar tam olarak şu bileşenleri içermeli: {', '.join(RISK_COMPONENTS)}")
        if any((not math.isfinite(x)) or x < 0 or x > 1 for x in v.values()):
            raise ValueError("her ağırlık 0 ile 1 arasında olmalı")
        if abs(sum(v.values()) - 1.0) > 1e-6:
            raise ValueError(f"ağırlıkların toplamı 1.00 olmalı (şu an {sum(v.values()):.3f})")
        return {k: round(float(v[k]), 6) for k in RISK_COMPONENTS}

    @field_validator("ramps")
    @classmethod
    def _r(cls, v: dict[str, list[float]]) -> dict[str, list[float]]:
        if set(v) != set(RAMPS):
            raise ValueError(f"eşikler tam olarak şu sinyalleri içermeli: {', '.join(RAMPS)}")
        for k, pair in v.items():
            lo_b, hi_b = RAMPS[k]
            if len(pair) != 2 or not all(math.isfinite(x) for x in pair):
                raise ValueError(f"{k}: [değer_sıfır, değer_bir] iki sayı olmalı")
            if not (lo_b <= pair[0] < pair[1] <= hi_b):
                raise ValueError(f"{k}: {lo_b} ≤ değer_sıfır < değer_bir ≤ {hi_b} olmalı")
        return {k: [float(x) for x in v[k]] for k in RAMPS}

    @field_validator("bands")
    @classmethod
    def _b(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != {"low", "medium", "high"}:
            raise ValueError("bantlar low, medium, high içermeli")
        if not (0 < v["low"] < v["medium"] < v["high"] < 100):
            raise ValueError("0 < low < medium < high < 100 olmalı")
        return {k: float(v[k]) for k in ("low", "medium", "high")}


# ------------------------------------------------------------------------------------------------ llm
class LlmSettings(_Section):
    chat_profile: str | None = Field(None, max_length=64, json_schema_extra=_meta(
        help="Soru yorumlama, yönlendirme ve sohbet cevabı için profil. Boş = ortam (CLAUDE_MODEL)"))
    rca_profile: str | None = Field(None, max_length=64, json_schema_extra=_meta(
        help="RCA açıklaması (explain adımı) için profil. Boş = ortam (CLAUDE_RCA_MODEL)"))
    model_override: str | None = Field(None, max_length=64, json_schema_extra=_meta(
        help="Üst bardaki model seçimi; seçiliyse iki görevin de modelini belirler"))


# ------------------------------------------------------------------------------------------------ observability
class ObservabilitySettings(_Section):
    langsmith_enabled: bool = Field(False, json_schema_extra=_meta(
        env="LANGSMITH_TRACING", help="Agent trace'lerini LangSmith'e gönder (girdi/çıktılar redakte edilir). "
                                      "İlk açılışta tracer kurulmadıysa açmak yeniden başlatma gerektirir"))
    langsmith_project: str = Field("airlab-sre-agent", min_length=1, max_length=100, json_schema_extra=_meta(
        env="LANGSMITH_PROJECT", apply="restart", help="LangSmith project adı"))
    sampling_rate: float = Field(1.0, ge=0.0, le=1.0, json_schema_extra=_meta(
        help="Trace gönderilecek koşu/tur oranı (1 = hepsi)"))
    environment_tag: str = Field("lab", min_length=1, max_length=32, json_schema_extra=_meta(
        env="SRE_ENVIRONMENT", help="Trace metadata'sına eklenen ortam etiketi"))


# ------------------------------------------------------------------------------------------------ notifications
class NotificationSettings(_Section):
    slack_enabled: bool = Field(False, json_schema_extra=_meta(
        env="SRE_SLACK_WEBHOOK_URL", help="Risk score threshold'unu aşan tamamlanmış RCA'ları Slack'e bildir"))
    slack_webhook_ref: str | None = Field(None, json_schema_extra=_meta(
        env="SRE_SLACK_WEBHOOK_URL", help="Slack Incoming Webhook adresinin secret referansı"))
    slack_risk_threshold: float = Field(75.0, ge=0, le=100, json_schema_extra=_meta(
        env="SRE_SLACK_RISK_THRESHOLD", help="Bu risk score ve üstü bildirilir"))

    # P2.3 SIEM iletimi (audit olayları → HTTPS webhook). Varsayılan kapalı.
    siem_enabled: bool = Field(False, json_schema_extra=_meta(
        apply="next_tick", help="Audit olaylarını SIEM'e (HTTPS webhook, JSON) ilet. İlk açılışta geçmiş gönderilmez"))
    siem_url_ref: str | None = Field(None, json_schema_extra=_meta(
        help="SIEM webhook adresinin secret referansı (ör. Splunk HEC / Elastic / Sentinel ara katmanı)"))
    siem_signing_ref: str | None = Field(None, json_schema_extra=_meta(
        help="İsteğe bağlı: gövdeyi HMAC-SHA256 ile imzalamak için secret referansı (X-SRE-Signature)"))
    siem_batch_size: int = Field(200, ge=10, le=1000, json_schema_extra=_meta(
        apply="next_tick", help="Bir istekte gönderilen en çok olay"))
    # P2.3 güvenlik alarmları (kodda tanımlı kurallar, eşik + pencere). Varsayılan kapalı.
    security_alerts_enabled: bool = Field(False, json_schema_extra=_meta(
        apply="next_tick", help="Güvenlik alarm kurallarını değerlendir; alarm audit'e (security.alert) ve "
                                "Slack webhook tanımlıysa Slack'e gider"))
    alert_window_minutes: int = Field(15, ge=5, le=1440, json_schema_extra=_meta(
        apply="next_tick", help="Sayım penceresi; aynı kural bu süre içinde bir kez alarm üretir", unit="dk"))
    alert_login_failures: int = Field(10, ge=1, le=10000, json_schema_extra=_meta(
        apply="next_tick", help="Başarısız / engellenen giriş threshold'u"))
    alert_authz_denied: int = Field(20, ge=1, le=10000, json_schema_extra=_meta(
        apply="next_tick", help="Yetki reddi (403) threshold'u"))
    alert_apikey_rejected: int = Field(10, ge=1, le=10000, json_schema_extra=_meta(
        apply="next_tick", help="Reddedilen API anahtarı threshold'u"))
    alert_policy_denied: int = Field(5, ge=1, le=10000, json_schema_extra=_meta(
        apply="next_tick", help="Policy reddi threshold'u (tool / koşu / dış bağlantı / model / LLM bütçesi)"))

    @field_validator("slack_webhook_ref", "siem_url_ref", "siem_signing_ref")
    @classmethod
    def _ref(cls, v):
        if v is not None and not SECRET_REF_RE.match(v):
            raise ValueError("secret referansı env:AD veya secret:ad biçiminde olmalı")
        return v

    @model_validator(mode="after")
    def _need_ref(self):
        if self.slack_enabled and not self.slack_webhook_ref:
            raise ValueError("Slack açıkken webhook secret referansı gerekli")
        if self.siem_enabled and not self.siem_url_ref:
            raise ValueError("SIEM açıkken webhook secret referansı gerekli")
        return self


# ------------------------------------------------------------------------------------------------ retention
class RetentionSettings(_Section):
    investigations_days: int = Field(180, ge=7, le=3650, json_schema_extra=_meta(
        env="SRE_RETENTION_DAYS", apply="next_tick",
        help="Analiz raporu + evidence (incident'a bağlı, RCA'lı, feedback'li veya sohbette olanlar saklanır)", unit="gün"))
    watch_events_days: int = Field(180, ge=7, le=3650, json_schema_extra=_meta(
        env="SRE_RETENTION_DAYS", apply="next_tick", help="Watch olay günlüğü", unit="gün"))
    llm_runs_days: int = Field(180, ge=7, le=3650, json_schema_extra=_meta(
        env="SRE_RETENTION_DAYS", apply="next_tick", help="LLM Monitoring özetleri", unit="gün"))
    job_runs_days: int = Field(30, ge=7, le=3650, json_schema_extra=_meta(
        apply="next_tick", help="Zamanlanmış iş çalıştırma geçmişi (operational log)", unit="gün"))
    # P2.7 ajan hafızası (LangGraph). Muhafazakâr varsayılanlar: yalnız sahibi silinmiş veri otomatik temizlenir.
    orphan_checkpoints_cleanup: bool = Field(True, json_schema_extra=_meta(
        apply="next_tick", help="Investigation'ı ya da konuşması artık olmayan thread'lerin checkpoint'lerini sil "
                                "(son checkpoint en az 1 gün önce)"))
    checkpoints_days: int = Field(0, ge=0, le=3650, json_schema_extra=_meta(
        apply="next_tick", help="Bu süreden eski thread checkpoint'lerini sil (aktif investigation hariç). "
                                "0 = kapalı; açılırsa en az 7", unit="gün"))
    store_days: int = Field(0, ge=0, le=3650, json_schema_extra=_meta(
        apply="next_tick", help="Bu süredir güncellenmeyen Store öğelerini sil. 0 = kapalı; açılırsa en az 7. "
                                "Süresi dolmuş (expires_at) öğeler her zaman silinir", unit="gün"))

    @field_validator("checkpoints_days", "store_days")
    @classmethod
    def _zero_or_week(cls, v):
        if 0 < v < 7:
            raise ValueError("0 (kapalı) ya da en az 7 gün olmalı")
        return v


# ------------------------------------------------------------------------------------------------ memory
class MemorySettings(_Section):
    """Agent Memory (agent: sre-agent). Varsayılanlar mevcut davranışı korur: sohbet retrieval + context injection
    açık; analiz akışına memory, otomatik extraction kapalı. Risk skoru memory'den hiçbir zaman etkilenmez."""
    memory_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Ana anahtar. Kapalıyken long-term memory okunmaz/yazılmaz ve context'e eklenmez; "
             "checkpoint (durable execution) etkilenmez"))
    memory_observability_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Memory event'leri ve retrieval kayıtları (yalnız gözlem; agent davranışını değiştirmez)"))
    short_term_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Sohbette konuşma odağı ve önceki tur özetleri (working memory) takip sorularında kullanılır"))
    long_term_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Geçmiş kayıtlar (episode, investigation, incident, RCA, runbook, known issue, semantic) okunur"))
    semantic_enabled: bool = Field(True, json_schema_extra=_meta(help="Semantic memory (known issue + doğrulanmış olgular)"))
    episodic_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Episodic memory (episode, geçmiş investigation / incident / RCA)"))
    procedural_enabled: bool = Field(True, json_schema_extra=_meta(help="Procedural memory (onaylı runbook'lar)"))
    auto_extraction_enabled: bool = Field(False, json_schema_extra=_meta(
        help="Feedback ile doğrulanmış investigation'lardan servis bağımlılığı olgusu çıkar (unverified; "
             "doğrulanana kadar retrieval dışı)"))
    memory_retrieval_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Sohbetin recall adımı memory'de arama yapar"))
    context_injection_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Bulunan kayıtlar LLM isteğine (history/memory bölümü) eklenir. Kapalıyken retrieval yine kaydedilir"))
    analysis_memory_enabled: bool = Field(False, json_schema_extra=_meta(
        help="Investigation sonunda benzer episode, semantic topology ve onaylı runbook'lar rapora 'historical "
             "context' olarak eklenir (evidence değildir, risk skorunu değiştirmez)"))
    retrieval_strategy: Literal["fts_fingerprint"] = Field("fts_fingerprint", json_schema_extra=_meta(
        help="Agent'ın kullandığı strateji: tam metin araması + belirti parmak izi benzerliği. Semantic (vector) "
             "search implement edilmedi"))
    top_k: int = Field(5, ge=1, le=20, json_schema_extra=_meta(help="Kaynak başına en çok kayıt"))
    similarity_threshold: float = Field(0.35, ge=0.0, le=1.0, json_schema_extra=_meta(
        help="Episode parmak izi benzerliği alt sınırı (Jaccard tabanlı; confidence değildir)"))
    max_retrieved_records: int = Field(0, ge=0, le=200, json_schema_extra=_meta(
        help="Context'e eklenecek toplam kayıt sınırı. 0 = sınır yok"))
    max_context_tokens: int = Field(0, ge=0, le=100000, json_schema_extra=_meta(
        help="Memory bölümü için tahmini token bütçesi (karakter/4). 0 = sınır yok; aşılırsa kayıtlar kırpılır"))
    memory_retention_days: int = Field(0, ge=0, le=3650, json_schema_extra=_meta(
        apply="next_tick", help="Arşivlenmiş / süresi dolmuş memory kayıtlarını bu süreden sonra sil. 0 = silme",
        unit="gün"))
    memory_event_retention_days: int = Field(90, ge=7, le=3650, json_schema_extra=_meta(
        apply="next_tick", help="Memory event ve retrieval kayıtları", unit="gün"))
    default_ttl_days: int = Field(0, ge=0, le=3650, json_schema_extra=_meta(
        help="Yeni semantic kayıtlara varsayılan expires_at. 0 = süresiz", unit="gün"))
    consolidation_policy: Literal["off"] = Field("off", json_schema_extra=_meta(
        help="Consolidation implement edilmedi (otomatik birleştirme yok)"))
    deduplication_policy: Literal["content_hash"] = Field("content_hash", json_schema_extra=_meta(
        help="Aynı agent + namespace içinde normalize içerik hash'i aynı olan aktif kayıt ikinci kez yazılmaz"))
    auto_archive_expired: bool = Field(True, json_schema_extra=_meta(
        apply="next_tick", help="expires_at geçen kayıtlar 'expired' olur ve retrieval dışı kalır"))
    require_validation: bool = Field(True, json_schema_extra=_meta(
        help="Yazmadan önce prompt injection / poisoning kontrolü; eşleşen kayıt reddedilir"))
    require_evidence_verification: bool = Field(True, json_schema_extra=_meta(
        help="Güvenilmeyen kaynaktan (agent extraction) gelen kayıt doğrulanana kadar retrieval dışı"))
    require_procedural_approval: Literal[True] = Field(True, json_schema_extra=_meta(
        help="Procedural memory her zaman onay ister (four-eyes); kapatılamaz"))
    pii_redaction_enabled: bool = Field(True, json_schema_extra=_meta(help="Yazılan içerikte PII maskelenir"))
    secret_redaction_enabled: Literal[True] = Field(True, json_schema_extra=_meta(
        help="Secret içeren içerik reddedilir; kapatılamaz"))
    poisoning_protection_enabled: bool = Field(True, json_schema_extra=_meta(
        help="Güvenilmeyen kaynak procedural memory'ye yazamaz ve kendini 'verified' işaretleyemez"))


SECTIONS: dict[str, type[_Section]] = {
    "general": GeneralSettings, "agent": AgentSettings, "scheduling": SchedulingSettings, "risk": RiskSettings,
    "llm": LlmSettings, "observability": ObservabilitySettings, "notifications": NotificationSettings,
    "retention": RetentionSettings, "agent_memory": MemorySettings,
}


LABELS = {
    "display_name": "Platform adı", "timezone": "Timezone", "language": "Dil", "date_format": "Tarih biçimi",
    "enabled": "Agent etkin", "execution_mode": "Execution mode", "incident_triggered": "Incident-triggered investigation",
    "analysis_window_minutes": "Varsayılan analiz penceresi", "baseline_minutes": "Baseline uzunluğu",
    "max_window_minutes": "En uzun pencere", "run_timeout_s": "Investigation Timeout",
    "max_concurrent_runs": "Eşzamanlı investigation", "max_extra_iterations": "Ek evidence turu",
    "recursion_limit": "LangGraph recursion_limit", "max_queries_per_run": "Koşu başına query bütçesi",
    "query_timeout_s": "Query Timeout", "query_retries": "Query retry", "llm_enabled": "LLM kullan",
    "llm_max_calls_per_run": "Koşu başına LLM çağrısı", "llm_max_tokens_per_run": "Koşu başına LLM token",
    "scheduled_llm": "Zamanlanmış koşularda LLM", "incident_llm": "Incident koşularında LLM",
    "watch_confirm_windows": "Watch onay penceresi", "watch_cooldown_minutes": "Watch cooldown",
    "health_check_enabled": "Health check", "health_check_interval_minutes": "Health check aralığı",
    "health_check_window_minutes": "Health check penceresi", "watch_enabled": "Watch",
    "watch_poll_seconds": "Watch aralığı", "langsmith_sync_enabled": "LangSmith senkronu",
    "langsmith_sync_minutes": "Senkron aralığı", "weights": "Bileşen ağırlıkları", "ramps": "Normalizasyon aralıkları",
    "bands": "Risk bantları", "min_score_coverage": "Minimum coverage", "chat_profile": "Chat profili",
    "rca_profile": "RCA profili", "model_override": "Model seçimi (üst bar)", "langsmith_enabled": "LangSmith tracing",
    "langsmith_project": "LangSmith project", "sampling_rate": "Sampling oranı", "environment_tag": "Environment etiketi",
    "slack_enabled": "Slack bildirimi", "slack_webhook_ref": "Slack webhook secret", "slack_risk_threshold": "Risk score threshold",
    "siem_enabled": "SIEM iletimi", "siem_url_ref": "SIEM webhook secret", "siem_signing_ref": "SIEM imza secret",
    "siem_batch_size": "SIEM batch boyutu", "security_alerts_enabled": "Güvenlik alarmları",
    "alert_window_minutes": "Alarm penceresi", "alert_login_failures": "Threshold: başarısız giriş",
    "alert_authz_denied": "Threshold: yetki reddi", "alert_apikey_rejected": "Threshold: API anahtarı reddi",
    "alert_policy_denied": "Threshold: policy reddi",
    "investigations_days": "Investigation kayıtları", "watch_events_days": "Watch olayları",
    "llm_runs_days": "LLM Monitoring özetleri", "job_runs_days": "İş geçmişi",
    "orphan_checkpoints_cleanup": "Sahipsiz checkpoint temizliği", "checkpoints_days": "Checkpoint TTL",
    "store_days": "Store TTL",
    "memory_enabled": "Enable Memory", "memory_observability_enabled": "Enable Memory Observability",
    "short_term_enabled": "Enable Short-Term Memory", "long_term_enabled": "Enable Long-Term Memory",
    "semantic_enabled": "Enable Semantic Memory", "episodic_enabled": "Enable Episodic Memory",
    "procedural_enabled": "Enable Procedural Memory", "auto_extraction_enabled": "Enable Automatic Memory Extraction",
    "memory_retrieval_enabled": "Enable Memory Retrieval", "context_injection_enabled": "Enable Context Injection",
    "analysis_memory_enabled": "Memory in Incident Analysis", "retrieval_strategy": "Retrieval Strategy",
    "top_k": "Top K", "similarity_threshold": "Similarity Threshold",
    "max_retrieved_records": "Maximum Retrieved Records", "max_context_tokens": "Maximum Memory Context Tokens",
    "memory_retention_days": "Memory Retention Period", "memory_event_retention_days": "Memory Event Retention Period",
    "default_ttl_days": "TTL", "consolidation_policy": "Consolidation Policy",
    "deduplication_policy": "Deduplication Policy", "auto_archive_expired": "Automatic Archiving",
    "require_validation": "Require Memory Validation", "require_evidence_verification": "Require Evidence Verification",
    "require_procedural_approval": "Require Procedural Memory Approval", "pii_redaction_enabled": "Enable PII Redaction",
    "secret_redaction_enabled": "Enable Secret Redaction",
    "poisoning_protection_enabled": "Enable Memory Poisoning Protection",
}


def _kind(p: dict) -> tuple[str, list | None]:
    """JSON schema özelliği → arayüz alan türü (+ seçenekler)."""
    opts = p.get("enum") or ([p["const"]] if "const" in p else None)
    if opts:
        return "enum", list(opts)
    typ = p.get("type") or next((x.get("type") for x in p.get("anyOf", []) if x.get("type") != "null"), "string")
    return {"boolean": "bool", "integer": "int", "number": "float", "object": "object"}.get(typ, "string"), None


def field_meta(section: str) -> dict[str, dict[str, Any]]:
    model = SECTIONS[section]
    props = model.model_json_schema()["properties"]
    out = {}
    for name, f in model.model_fields.items():
        extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
        p = props.get(name, {})
        kind, options = _kind(p)
        out[name] = {"env": extra.get("env"), "apply": extra.get("apply", "next_run"), "help": extra.get("help", ""),
                     "unit": extra.get("unit"), "label": LABELS.get(name, name), "kind": kind, "options": options,
                     "min": p.get("minimum"), "max": p.get("maximum"),
                     "nullable": any(x.get("type") == "null" for x in p.get("anyOf", []))}
    return out


# ------------------------------------------------------------------------------------------------ profiller
CONNECTOR_PROVIDERS = {
    # analysis=True: analiz grafiğine veri kaynağı olabilir (doğrulanmış adapter). Diğerleri yalnız test/keşif.
    "dynatrace": {"label": "Dynatrace (Remote MCP)", "analysis": True, "adapter": "DQL (execute-dql)"},
    "custom": {"label": "Özel MCP sunucusu", "analysis": False, "adapter": None},
}
LLM_PROVIDERS = {
    "anthropic": {"label": "Anthropic", "supported": True},
    "openai": {"label": "OpenAI", "supported": False},
    "google": {"label": "Google Gemini", "supported": False},
    "azure_openai": {"label": "Azure OpenAI", "supported": False},
    "ollama": {"label": "Ollama", "supported": False},
    "openai_compatible": {"label": "OpenAI-compatible", "supported": False},
}
NAME_RE = r"^[a-z0-9][a-z0-9_-]{1,63}$"


class ConnectorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    provider: Literal["dynatrace", "custom"] = "custom"
    transport: Literal["streamable_http"] = "streamable_http"
    endpoint: str = Field(min_length=8, max_length=500)
    auth_type: Literal["bearer", "none"] = "bearer"
    secret_ref: str | None = None
    timeout_s: int = Field(60, ge=1, le=300)
    retries: int = Field(2, ge=0, le=5)
    enabled: bool = True
    read_only: bool = True
    analysis_source: bool = False
    allowed_tools: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("secret_ref")
    @classmethod
    def _ref(cls, v):
        if v is not None and not SECRET_REF_RE.match(v):
            raise ValueError("secret referansı env:AD veya secret:ad biçiminde olmalı")
        return v

    @model_validator(mode="after")
    def _rules(self):
        if self.auth_type == "bearer" and not self.secret_ref:
            raise ValueError("bearer kimlik doğrulaması için secret referansı gerekli")
        if self.analysis_source and not CONNECTOR_PROVIDERS[self.provider]["analysis"]:
            raise ValueError(f"'{self.provider}' için doğrulanmış analiz adapter'ı yok; analiz kaynağı yapılamaz")
        if self.analysis_source and not self.read_only:
            raise ValueError("analiz kaynağı read-only olmalı")
        return self


class ConnectorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    endpoint: str | None = Field(None, min_length=8, max_length=500)
    auth_type: Literal["bearer", "none"] | None = None
    secret_ref: str | None = None
    timeout_s: int | None = Field(None, ge=1, le=300)
    retries: int | None = Field(None, ge=0, le=5)
    enabled: bool | None = None
    read_only: bool | None = None
    analysis_source: bool | None = None
    allowed_tools: list[str] | None = Field(None, max_length=200)


class LlmProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    provider: str = "anthropic"
    model: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    base_url: str | None = Field(None, max_length=500)
    secret_ref: str = Field(...)
    timeout_s: int = Field(120, ge=5, le=600)
    max_tokens: int = Field(16000, ge=256, le=128000)
    effort: Literal["low", "medium", "high"] | None = "medium"
    retries: int = Field(2, ge=0, le=5)
    monthly_budget_usd: float | None = Field(None, gt=0, le=100000)
    enabled: bool = True

    @field_validator("provider")
    @classmethod
    def _prov(cls, v):
        if v not in LLM_PROVIDERS:
            raise ValueError("bilinmeyen provider")
        if not LLM_PROVIDERS[v]["supported"]:
            raise ValueError(f"{LLM_PROVIDERS[v]['label']} için bu deployment'ta doğrulanmış adapter yok")
        return v

    @field_validator("secret_ref")
    @classmethod
    def _ref(cls, v):
        if not SECRET_REF_RE.match(v):
            raise ValueError("secret referansı env:AD veya secret:ad biçiminde olmalı")
        return v


class LlmProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    model: str | None = Field(None, min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:/-]+$")
    base_url: str | None = Field(None, max_length=500)
    secret_ref: str | None = None
    timeout_s: int | None = Field(None, ge=5, le=600)
    max_tokens: int | None = Field(None, ge=256, le=128000)
    effort: Literal["low", "medium", "high"] | None = None
    retries: int | None = Field(None, ge=0, le=5)
    monthly_budget_usd: float | None = Field(None, ge=0, le=100000)      # 0 = sınırı kaldır
    enabled: bool | None = None
