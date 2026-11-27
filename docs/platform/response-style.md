# Yanıt üslubu: sohbet yanıtı, analiz açıklaması ve RCA özeti

Kullanıcıya gösterilen LLM metinleri, nöbet devrinde meslektaşına yazan bir SRE'nin üslubunu taşır: ilk cümle ana
bulguyu söyler; ölçüm, hipotez ve doğrulanmış neden ayrı ifade edilir; ölçülen sıfır, veri yokluğu ve başarısız query
karıştırılmaz.

## Kurallar nerede?

| Katman | Dosya | Kapsam |
|---|---|---|
| Ortak üslup | `sre-agent/src/airlab_agent/style.py` (`STYLE_PROMPT`) | Kullanıcıya metin üreten **her** system prompt |
| Terminology policy | `sre-agent/src/airlab_agent/terminology.py` (`POLICY_PROMPT`) | Aynı; çelişkide son söz bundadır |
| Analiz açıklaması | `airlab_agent/llm.py` (`SYSTEM_EXPLAIN_BASE`) | Veri sözleşmesi ve biçim |
| Sohbet ekleri | `sre_platform/agent/prompts.py` | Geçmişle karşılaştırma, history ve follow-up yanıtları |
| RCA özeti | `sre_platform/services/rca_service.py` (`SUMMARY_SYSTEM`) | İki ya da üç kısa paragraf, en fazla ~220 kelime |

Ortak üslup tek yerden eklenir: `terminology.with_policy()` prompt'un kendi talimatlarından sonra üslup bloğunu, en
sona terminology policy'yi koyar (idempotent). Sıra: **prompt'un kendi talimatları → üslup → terminology policy**.

## Hangi üretim yolları bu kuralları alır?

| Yol | Son metni üreten | System prompt |
|---|---|---|
| Sohbet, analiz sorusu | `agent/chat.py` `respond` → `ClaudePort.astream` | `ANALYSIS_SYSTEM` |
| Sohbet, geçmiş kayıt sorusu | aynı | `HISTORY_SYSTEM` |
| Sohbet, takip sorusu | aynı | `FOLLOWUP_SYSTEM` |
| Manuel / zamanlanmış / watch investigation | `airlab_agent/graph.py` `explain` → `Llm.explain` | `SYSTEM_EXPLAIN` |
| RCA raporu özeti | `services/rca_service.generate` | `SUMMARY_SYSTEM` |
| Agent Builder agent'ı (LLM node'ları) | `builder/engine.py` `Runner._system` | agent talimatları + `with_policy` |

`ClaudePort.astream` ayrıca her çağrıda `with_policy` uygular; ileride eklenen bir prompt da üslubu alır.

Akan metin ile kaydedilen metin aynıdır: sohbet yanıtı token token iletilir, tur sonunda aynı metin mesaja yazılır
(guardrail metni düzeltirse istemciye `answer_replaced` gider). Eski kayıtlar olduğu gibi okunur; geriye dönük
yeniden yazım yoktur.

## Değişmeyenler

* Risk score, severity, data coverage ve confidence deterministik motordan gelir; prompt bunları değiştiremez.
  `final_output` guardrail'i evidence kimliği ve risk tutarlılığını kontrol etmeye devam eder.
* JSON / Pydantic sözleşmeleri (`AnalysisRequest`, yönlendirme çıktısı, Agent Builder structured output node'ları).
  Üslup bloğu açıkça "belirli bir çıktı biçimi ya da şema isteniyorsa ona uy" der.
* Ek LLM çağrısı yoktur; aynı çağrıların system prompt'u değişti.
* LLM kullanılmayan çalıştırmalardaki deterministik açıklamanın (`report.deterministic_explanation`) yapısı ve sayıları
  aynıdır; yalnız zaman ve karşılaştırma ifadeleri değişti (TSİ, "önceki dönem").
* Agent Builder agent'larının kendi system prompt'u ve node talimatları başta ve aynen kalır.

## Modele giden veri

Analiz verisine `true_zero_signals` eklendi. Model artık üç durumu veriden ayırt eder: `true_zero_signals` (ölçülen
sıfır), `data_gaps` içinde `EMPTY` / `STALE` (kaynak veri üretmedi) ve `ERROR` / `TIMEOUT` / `PERMISSION` /
`UNAVAILABLE` / `SKIPPED_BUDGET` (query yanıt vermedi).

## Zaman gösterimi

Kullanıcıya giden metinlerde zamanlar yalnız Türkiye saatiyle (TSİ) yazılır; UTC ayrıca belirtilmez. Kayıtlar, query
pencereleri ve API alanları UTC ISO-8601 kalır. Çeviri tek yerdedir: `airlab_agent/timefmt.py`.

* LLM'e giden her veri bloğu (`llm.data_json`) ISO zaman damgalarını `2026-11-21 15:15 TSİ` biçimine çevirir; model
  saat hesabı yapmaz, değeri aynen aktarır. DQL ve log metni içindeki zamanlara dokunulmaz.
* Deterministik açıklama, RCA bölümleri, investigation başlıkları ve bildirim metinleri aynı yardımcıyı kullanır.
* Yönlendirme (router) prompt'ları UTC ile çalışmaya devam eder: çıktıları kullanıcıya gösterilmez, `start_utc` / `end_utc`
  alanları sözleşmedir.
* Arayüzdeki zamanlar Settings › General'daki timezone ayarıyla gösterilir (varsayılan `Europe/Istanbul`).

## Ayrıntı düzeyi

Varsayılan yanıt ayrıntılıdır. İlk paragraf sonucu özetler; ardından sırayla servis etkisi, iki dönemin ölçüm
karşılaştırması (önceki değer, şimdiki değer, birim, fark), her bulguyu destekleyen evidence, doğrulanamayan bağlantılar
ve gerekiyorsa tek bir sonraki kontrol gelir. Kullanıcı açıkça kısa yanıt isterse ("kısaca", "özetle") yalnız özet
yazılır. Veri azsa metin uzatılmaz: eldeki bulgu ve eksik evidence yazılır.

* Kural ortak üslup bloğundadır (`style.STYLE_PROMPT`); sohbet, analiz açıklaması, RCA özeti ve Agent Builder agent'ları
  aynı kuralı alır.
* Analiz açıklaması (`SYSTEM_EXPLAIN_BASE`) en fazla ~450 kelime; RCA özeti iki ya da üç paragraf, ~220 kelime.
* RCA özet modeline bölümlerle birlikte `measurements` gider: iki dönemin uygulama ölçümleri
  (`rca_service._measurements`). Bu alan yalnız LLM girdisidir; RCA kaydına ve API yanıtına yazılmaz.
* RCA "Güven / belirsizlik" bölümü artık iki durumu ayırır: bekleyen ve bloklayan uygulamayı eşleyen blocking edge
  kaydı varsa bekleme ilişkisi doğrudan görülmüştür; yoksa sonuç correlation temelli hipotezdir. Blocking edge satırı,
  wait event'in bloklayan session'a ait olduğunu açıkça yazar.

## Zaman aralığı ve karşılaştırma dili

Üretilen metinde "pencere", "window" ve "baseline" kelimeleri kullanılmaz. Şema anahtarları (`windows[].name`,
`features_summary.baseline`), query'ler ve skor hesabı aynı kalır.

| İç terim | Metindeki ifade |
|---|---|
| Analiz penceresi | "15:05–15:15 arasında", "incelenen zaman aralığı" |
| Baseline penceresi | "karşılaştırma için kullanılan önceki dönem" (saatleri biliniyorsa yazılır) |
| Baseline değeri | "önceki dönemde ölçülen değer" |
| Baseline'dan sapma | "önceki döneme göre artış / azalış" |

"Olaydan önceki dönem" ve "normal seviye" ifadeleri kullanılmaz: karşılaştırma aralığının olaydan önce olduğu ya da
olağan işleyişi temsil ettiği veride doğrulanmış değildir. Bir ölçümün önceki dönem değeri yoksa metin "karşılaştırma
verisi yok" der; değer tahmin edilmez. Terminology policy'deki "baseline" zorunluluğu bu nedenle kaldırıldı.

Kapsam dışı: arayüzdeki sabit etiketler (ör. Analysis sayfasındaki "Baseline" / "Analiz penceresi" sütun başlıkları).
