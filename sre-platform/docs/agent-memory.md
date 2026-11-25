# Agent memory

Agent'ın hafızası dört katmandır; hepsi PostgreSQL'dedir (restart sonrası kalıcı). Bu belge episodic ve working
memory'yi anlatır. Checkpointer/Store işletimi için bkz. [settings.md](settings.md) (Retention & Memory).

| Katman | Nerede | Ne tutar | Ömür |
|---|---|---|---|
| Working memory | `langgraph.checkpoints` (konuşma `thread_id`) | son 20 mesaj, konuşma odağı (focus), tur özetleri (digests, en fazla 30) | konuşma |
| Episodic memory | `sre.agent_episodes` | investigation başına belirti parmak izi → hipotez → sonuç | investigation silinince silinir |
| İlişkisel kayıtlar | `sre.*` (investigations, incidents, rca, runbooks, known_issues, messages) | tek doğruluk kaynağı; tam metin araması | retention ayarı |
| Store | `langgraph.store` | kısa özetler (uygulama / kullanıcı / konuşma namespace'leri) | otomatik silinmez |

Hafıza kayıtları prompt'a `<..._data>` blokları içinde **veri** olarak verilir; içlerindeki metin talimat sayılmaz.
Episode alanları LLM çıktısından türetilmez: deterministik rapordan, kullanıcı feedback'inden ve incident kaydından
gelir. (Konuşma mesajları ve tur özetleri ise doğal olarak LLM cevaplarını içerir; bunlar yalnız o konuşmanın
bağlamıdır, ortam hakkında kalıcı bilgi olarak kullanılmaz.)

## Episodic memory

`repositories/episodes.py`. Her episode bir investigation'a bağlıdır (`investigation_id` birincil anahtar).

**Ne zaman yazılır** (`refresh()`, idempotent; hata olursa savepoint içinde kalır, asıl işlem sürer):

- investigation COMPLETED / PARTIAL olarak kaydedildiğinde (`investigations.complete`) — sohbet, manuel, health check,
  watch hepsi bu noktadan geçer,
- feedback geldiğinde (`rca.upsert_feedback`) → `outcome`,
- incident bağlandığında / `root_cause` veya aksiyonlar değiştiğinde / durum değiştiğinde → `resolution`, `resolved_at`.

Yalnız **kayda değer** koşular episode olur: impact, mechanism, bağlı incident ya da feedback varsa. Sorunsuz rutin
health check'ler episode üretmez (15 dakikada bir koşan işler hafızayı doldurmaz).

**Belirti parmak izi** (`fingerprint`): sorunun kelimelerinden bağımsız, rapordan deterministik etiketler:

| Etiket | Kaynak |
|---|---|
| `<app>:latency`, `<app>:errors`, `<app>:pool_timeout` | `impacted[app].reasons` |
| `source:<app>` | `causality.source_apps` |
| `lock:<blocker>><waiter>` | row lock `edge_pairs` (uygulamalar arası, en çok 5) |

**Outcome** (feedback çoğunluğu): `confirmed`, `partially_confirmed`, `rejected` (hipotez yanlış çıktı),
`unverified` (feedback yok). Eşitlik `partially_confirmed` sayılır.

**Benzerlik** (`similar`): `0.5 × belirti Jaccard + 0.35 × mechanism (aynı birincil 1, ortak 0.5) + 0.15 × uygulama
Jaccard`; eşik 0.35, son 180 gün. Aynı skorda doğrulanmış episode önce gelir. `rejected` episode'lar da döner: "geçen
sefer bu açıklama yanlış çıktı" bilgisi değerlidir ve prompt bunu söylemeyi ister.

**Kullanım** (`MemoryService.recall`):

- analyze / compare: güncel investigation'ın parmak iziyle benzer episode'lar → `<history_data>` içinde `episodes`
  (`similarity`, `shared_symptoms`, `outcome`, `resolution`) + `current_symptoms`,
- history soruları: metin / mechanism / uygulama araması; metin aramasında önce ilgililik, ardından `confirmed`
  ve çözüm kayıtlı olanlar,
- LLM kapalıyken deterministik cevap da episode'ları (outcome + çözüm kaydı) listeler.

Benzerlik bir ipucudur, aynı Root Cause'un evidence'ı değildir; prompt bunu açıkça yazdırır.

**Bilerek yapılmayanlar:** embedding / vektör arama yok (Anthropic API embedding sunmaz; ek sağlayıcı ve anahtar
gerekir). Parmak izi benzerliği analiz turlarında kelimeden bağımsızdır; history sorularında arama tam metin +
router'ın çıkardığı mechanism ile yapılır.

## Working memory

`agent/working_memory.py`, `agent/chat.py` (`ChatState.focus`, `ChatState.digests`).

**Focus** — konuşmanın o anki odağı: `apps`, `window` (son analiz penceresi, UTC), `mechanism`, `investigation`.
`route` adımında soruyla, `investigate` adımında gerçek pencere ve sonuçla güncellenir.

Takip sorusu eksik bilgi içeriyorsa odak devralınır:

| Soru | Davranış |
|---|---|
| "Peki aynı pencerede tekrar bak" | uygulama + pencere odaktan |
| "Peki 1 saat öncesi?" | uygulama odaktan, pencere 60 dk geriye kaydırılır |
| "Bunun benzeri daha önce oldu mu?" (history) | mechanism odaktan |
| "Checkin son 15 dakika" | soru açık → odak kullanılmaz |
| Arayüzde zaman aralığı seçili | pencere devralınmaz (arayüz seçimi önceliklidir) |

Devralma kullanıcıya `agent_thinking` olayıyla söylenir ("Önceki turun odağı kullanıldı — …"). LLM router'a odak
özeti de verilir; LLM'in boş bıraktığı alanlar deterministik kuralla doldurulur. Odak arayüzde sohbet başlığında
görünür ve **Odağı temizle** ile silinir (`DELETE /api/conversations/{id}/memory/focus`; süren/yarım kalan turda 409).

**Digests** — tur başına kısa özet (soru, niyet, investigation, mechanism, cevabın ilk 240 karakteri). Ham mesaj
penceresi 20 mesajla sınırlıdır; düşen eski turlar özetlerde kalır. Takip cevapları (`followup`) son 8 özeti ve
odağı `<context_data>` içinde alır. Özetler deterministiktir (ek LLM çağrısı / maliyet yok).

`GET /api/conversations/{id}/memory` (yalnız konuşma sahibi): `focus`, `digests` (en yeni önce),
`messages_in_state`, `pending`.

## Kurulum, migration ve rollback

```bash
cd /opt/airlab/app-vm
docker compose --profile platform build sre-migrate sre-frontend
docker compose --profile platform run --rm sre-migrate      # 0003 → 0004 + episode backfill (idempotent)
docker compose --profile platform up -d sre-backend sre-worker sre-frontend
```

Backfill episode'u olmayan tamamlanmış investigation'ları (en yeni 5000) işler; tekrar çalıştırmak güvenlidir.
Working memory için migration yoktur: alanlar checkpoint state'ine eklenir; eski konuşmalar ilk yeni turda
odak/özet kazanır.

**Rollback:** `docker compose --profile platform run --rm sre-migrate migrate --downgrade 0003` yalnız
`agent_episodes` tablosunu siler; kaynak kayıtlar etkilenmez. Önceki imaja dönülürse checkpoint'lerdeki `focus` /
`digests` alanları yok sayılır.

## Test

- `tests/test_episodic_working_memory.py`: parmak izi determinizmi, benzerlik/outcome, odak çözümleme kuralları,
  özet sınırı; episode yaşam döngüsü (feedback → rejected, incident çözümü → resolution, ikinci analizde farklı
  kelimelerle bulunma), sorunsuz koşuda episode olmaması, backfill idempotensi, turlar arası odak (uygulama +
  pencere kaydırma), restart sonrası özetler, API sahiplik kontrolü.
- `tests/test_settings.py`: 0004 downgrade/upgrade.
- `src/test/focusbar.test.tsx`: odak gösterimi, temizleme, salt okunur rol.
