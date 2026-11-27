# MCP Çağrıları sayfası

Ana menü › Platform › **MCP Çağrıları** (`/mcp-calls`). Agent'ın MCP sunucularına gönderdiği `tools/call` isteklerini,
aldığı yanıtın özetini ve çağrının hangi Agent çalışmasına ait olduğunu gösterir.

## 1. Mevcut yapı ve tercih edilen yol

| Bileşen | Durum | Karar |
|---|---|---|
| MCP istemcisi (`airlab_agent/mcp_gateway.py`) | Her mantıksal çağrının `mcp_call_id`'si, retry denemeleri ve sonucu zaten vardı | Kayıt bilgisi **gerçek istemcide** üretilir; ayrı bir kayıt katmanı yok |
| Çağrı kaydı (`sre.agent_tool_executions`) | Investigation başına tool kayıtları; kimlik = `mcp_call_id` = LangSmith span id | **Yeniden kullanıldı**; migration 0012 MCP kolonlarını ekledi |
| Agent run modeli | Her giriş noktası (chat, manuel, scheduler, watch) `InvestigationRunner` üzerinden `sre.investigations` satırı açar; `run_id` orada | Liste `run_id` ile join'lenir |
| Yetki | Permission modeli (`P.*`), API anahtarı scope'ları | `observability:read` (SRE/admin) |
| Frontend | URL filtreleri, tablo, sayfalama, Dialog bileşenleri | Aynı bileşenler |

Kapsam yalnız gönderilen `tools/call` çağrılarıdır. Gönderilmeyen çağrılar (DQL reddi, query budget doluşu) tool
kaydında kalır ama `mcp_method` boştur, bu sayfada görünmez.

## 2. Kayıt modeli (migration 0012)

`sre.agent_tool_executions` tablosuna eklenen kolonlar:

| Kolon | İçerik |
|---|---|
| `mcp_method` | `tools/call` (yalnız gönderilen çağrıda) |
| `connector` | `dynatrace` |
| `outcome` | `success`, `empty`, `timeout`, `permission_denied`, `tool_error`, `unavailable`, `cancelled` |
| `http_status` | Yanıt geldiyse 200. Hata durumunda exception zincirinden okunan kod (ör. 401); bilinmiyorsa boş |
| `attempts` | Her deneme için sıra numarası, süre, sonuç, HTTP kodu, exception tipi |
| `request_preview` | Güvenli istek önizlemesi (bkz. bölüm 3) |

Var olan satırlar migration sırasında yalnız sınıflandırılır (`mcp_method`, `connector`, `outcome`). Eski satırlardaki
`tool_arguments`'a dokunulmaz.

- `cancelled`: paralel bir çağrı MCP'yi erişilemez bulup oturumu kapattığında, uçuştaki çağrılar da artık bir kapanış
  olayı yayar. Böylece kayıt STARTED'da kalmaz.
- HTTP 200 içinde `isError=true` ile dönen MCP tool hatası `tool_error` ya da `permission_denied` olur ve başarısız
  sayılır.
- HTTP 401/403 yetki hatası tekrar denenmez.
- Kayıt sayısı yalnız `success`/`empty` sonuçlarda doludur; diğerlerinde boş gösterilir.

## 3. Saklanan ve saklanmayan veriler

İstek önizlemesi connector/tool bazlı alan listesiyle üretilir: `mcp_gateway.PREVIEW_FIELDS`.

```python
PREVIEW_FIELDS = {("dynatrace", "execute-dql"): {"raw": ("includeTypes",), "dql": "dqlQueryString"}}
```

- `raw` listesindeki parametrelerin yalnız sayı ya da bool değerleri saklanır.
- DQL parametresinden yalnız şunlar türetilir:
  - Query başlangıcı ve bitişi (`from:"…", to:"…"`)
  - `limit`
  - String literal'leri `"…"` ile maskelenmiş DQL şablonu
  - Karakter sayısı ve 16 karakterlik sha256 parmak izi
- Listede olmayan parametrelerin yalnız adı saklanır (`omitted_params`).
- Hata özeti hata metninin ilk satırıdır. Bu satırda string literal'ler maskelenir, secret'lar redakte edilir ve metin
  200 karakterle sınırlanır.

**Yazılmayanlar:** token, header, ham DQL, ham MCP yanıtı, Dynatrace kayıt/log içeriği. `tool_arguments` artık yalnız
`query_key`, `mcp_call_id` ve `langsmith_span` tutar.

Eski satırlarda ham DQL `tool_arguments.dqlQueryString`'de durabilir. API bu değeri hiçbir uçta döndürmez:

- `/mcp-calls/{id}` önizlemeyi sunucu tarafında üretir.
- `/investigations/{id}/tools` (`ToolOut`) değeri şablona çevirir.

Bu eski değerler migration 0013 ile veritabanından da silindi; bkz. R-MC1 ve bölüm 11.

## 4. API

Tüm uçlar `observability:read` ister. Kimliksiz istek 401, viewer ya da bu scope'u olmayan API anahtarı 403 alır.

| Uç | Açıklama |
|---|---|
| `GET /api/mcp-calls` | Filtreler: `since`, `until`, `connector`, `tool`, `status` (virgülle birden çok), `run_id`. Sunucu tarafı sayfalama (`page`, `size` ≤ 200); en yeni çağrı üstte |
| `GET /api/mcp-calls/facets` | Filtre seçenekleri: connector'lar, tool'lar, durumlar |
| `GET /api/mcp-calls/{id}` | Detay: `request`, `query` (şablon ve pencere), `response`, `attempts_log`, `related` |

Query penceresi şöyle belirlenir:

- `baseline`: query aralığı investigation'ın baseline aralığının içindeyse.
- `incident`: analiz penceresiyle kesişiyorsa.
- `custom`: ikisi de değilse.

## 5. Arayüz

**Liste sütunları:** başlangıç zamanı, connector, tool (altında query anahtarı), Agent adımı, durum (retry varsa
"N deneme"), süre, kayıt sayısı.

- Filtreler URL'de tutulur, seçili çağrı dahil (`?call=<id>`); bu yüzden link paylaşılabilir.
- Satıra tıklayınca detay açılır. Detay bölümleri: İstek, Dynatrace query, Yanıt özeti (evidence bağlantısı
  `?tab=evidence&ev=<key>`), Denemeler, İlişkili çalışma (investigation ve LangSmith span bağlantıları).

Investigation detayında "MCP çağrıları" butonu bu sayfayı `run_id` filtresiyle açar; buton SRE ve admin'e görünür.
Investigation'daki Tool executions tablosu artık ham DQL yerine maskeli şablonu gösterir.

## 6. Hata dayanıklılığı

Çağrı kaydının yazılamaması Agent'ın sonucunu değiştirmez. `InvestigationRunner._handle` DB hatasını loglayıp
devam eder; SSE olayları yine yayılır.

Correlation query'lerinde MCP erişilemese bile gönderilen çağrılar kayda geçer. Olaylar `finally` içinde işlenir ve
adım `traces` olarak kaydedilir.

## 7. Doğrulama

| Senaryo | Test |
|---|---|
| Başarılı çağrı (kayıt sayısı, evidence, HTTP 200) | `backend/tests/test_mcp_calls.py::test_call_classes_retry_detail_filters_and_no_raw_data` |
| Başarılı boş sonuç (kayıt 0, başarısız değil) | aynı test |
| Retry: tek satır, detayda iki deneme (timeout → success) | aynı test |
| HTTP 200 içinde MCP tool hatası ve yetki hatası → başarısız, kayıt sayısı boş | aynı test |
| Filtreler, sunucu tarafı sayfalama, sıralama, geçersiz durum 422 | aynı test |
| Ham DQL, log içeriği ve token DB'de ve API'de yok | aynı test |
| Timeout (tüm denemeler), HTTP 401 (tek deneme); açık kayıt kalmaz | `test_timeout_and_http_401` |
| Kayıt yazılamazsa analiz durumu ve risk aynı | `test_record_write_failure_does_not_change_result` |
| Yetkisiz erişim (kimliksiz 401, viewer 403, scope'suz API anahtarı 403, SRE ve scope'lu anahtar 200, olmayan çağrı 404) | `test_unauthorized_access` |
| Önizleme alan listesi, şablon, hata özeti, HTTP kodu, iptal edilen çağrının kapanış olayı | `sre-agent/tests/test_mcp_call_record.py` |
| Liste ve detay arayüzü, `run_id` filtresinin sunucuya gitmesi | `frontend/src/test/mcpcalls.test.tsx` |

Çağrılar testlerde gerçek `McpGateway` üzerinden kaydedilir; yalnız MCP oturumu sahtedir.

**Sonuçlar:**
- sre-agent: 134 test geçti.
- Backend: 141 test geçti. Koşu CI ile aynı koşullarda yapıldı: env dosyası yok, tek kullanımlık PostgreSQL,
  terminoloji kontrolü dahil.
- Frontend: tsc temiz, vitest 46/46, build başarılı.

## 8. Değişen dosyalar

- `sre-agent/src/airlab_agent/mcp_gateway.py`: `PREVIEW_FIELDS`, `request_preview`, `dql_template`,
  `safe_error_summary`, `http_status_of`, deneme başına HTTP kodu, `cancelled` kapanış olayı, 401/403'te retry yok.
- `sre-platform/backend/sre_platform/db/migrations/versions/0012_mcp_calls.py`, `db/models.py`
- `sre-platform/backend/sre_platform/repositories/investigations.py`: `mcp=` alanları
- `sre-platform/backend/sre_platform/agent/investigation.py`: ham DQL yerine önizleme, hata dayanıklılığı,
  correlation kaydı
- `sre-platform/backend/sre_platform/api/routes/mcp_calls.py` (yeni), `api/app.py`, `api/schemas.py` (`ToolOut`)
- `sre-platform/frontend/src/pages/McpCallsPage.tsx` (yeni), `App.tsx`, `layouts/AppLayout.tsx`, `hooks/queries.ts`,
  `types/api.ts`, `components/RecordTables.tsx`, `pages/InvestigationDetailPage.tsx`
- Testler: `sre-agent/tests/test_mcp_call_record.py`, `sre-platform/backend/tests/test_mcp_calls.py`,
  `sre-platform/frontend/src/test/mcpcalls.test.tsx`, `backend/tests/test_settings.py` (head 0012)

## 9. Bilinen sınırlar

- **R-MC1 (kapatıldı, 2026-11-26):** 0012 öncesi satırlardaki ham DQL migration 0013 ile silindi; yerine güvenli
  önizleme üretildi (`request_preview.backfilled = true`). Geri alınamaz; yalnız bu tarihten önceki DB yedeklerinde
  ham DQL bulunur.
- **R-MC2:** MCP Python istemcisi başarılı yanıtın HTTP kodunu dışarı vermez. Yanıt geldiyse 200 kabul edilir; hata
  kodları exception zincirinden okunur, okunamazsa boş kalır.
- **R-MC3:** Evidence kayıtlarının `query_reference` alanı bu işin kapsamında değildir; evidence görünümü değişmedi.
- **R-MC4:** Erişim modeli permission tabanlıdır. Investigation'lar ekip geneli görünür, bu yüzden çağrılar da
  `observability:read` sahibi herkes için görünür; investigation sahipliğine göre ayrı filtre yoktur.

## 10. Deploy (2026-11-26)

APP_VM'ye deploy edildi; rollback image'ları `:pre-mc`. `alembic_version` = 0012; 3644 mevcut MCP kaydı
sınıflandırıldı (sınıfsız kayıt 0). Servisler healthy.

- `/api/mcp-calls` kimliksiz istekte 401 dönüyor; `/mcp-calls` sayfası 200.
- Liste, detay ve facet handler'ları container içinde canlı DB ile çalıştırıldı (salt okunur). Eski bir kayıtta ham DQL
  DB'de duruyor (R-MC1), API çıktısında yok.

## 11. Eski ham DQL temizliği (2026-11-26, kullanıcı onaylı)

Migration 0013 uygulandı; rollback image'ı `:pre-scrub` (veri geri gelmez). Canlı sonuç:

- 3644 kayıttan ham DQL silindi; `dqlQueryString` içeren kayıt 0, önizlemesi olmayan MCP kaydı 0.
- API kontrolü: eski kayıt artık sunucu tarafında üretilen değil kayıtlı önizlemeyle dönüyor; ham DQL ne DB'de ne
  API'de var.
- Test: `test_migration_0013_scrubs_legacy_raw_dql`; backend 142/142.
