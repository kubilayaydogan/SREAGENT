SRE Platform projesinin mevcut frontend kaynak kodlarını uçtan uca incele ve kullanıcı arayüzünü profesyonel bir UI/UX tasarım anlayışıyla yeniden tasarla.

**Temel hedef**

Platform, AI tarafından oluşturulmuş jenerik bir dashboard gibi görünmemelidir. Deneyimli bir product designer ve senior frontend engineer tarafından geliştirilmiş, production-ready bir Enterprise SRE & Observability Platform görünümüne sahip olmalıdır.

Linear, Grafana, Datadog, Dynatrace ve Vercel'in tasarım prensiplerinden ilham al ancak hiçbirinin arayüzünü doğrudan kopyalama.

**1. Design System**

- Modern, minimalist, sade ve kurumsal bir tasarım oluştur.
- Göz yormayan nötr renk paleti kullan.
- Light Mode ve Dark Mode arasında tutarlı bir tasarım sistemi oluştur.
- Inter veya mevcut projeye uygun profesyonel bir font kullan.
- Sayısal metriklerde tabular numbers kullan.
- Tutarlı spacing, typography, border radius ve elevation standartları belirle.
- Merkezi design token yapısı oluştur.
- Gereksiz gradient, neon renk, glow, glassmorphism ve yoğun gölge kullanımını kaldır.
- Büyük ve aşırı yuvarlatılmış kartlardan kaçın.
- Emoji yerine Lucide gibi tutarlı bir icon set kullan.
- Her içeriği ayrı bir card içerisine yerleştirme.

**2. Navigation**

- Sade ve daraltılabilir bir sidebar oluştur.
- Aktif menü anlaşılır olsun.
- Breadcrumb ve page header yapıları tutarlı olsun.
- Navigasyon ekran alanını gereksiz tüketmesin.
- Mevcut işlevleri mantıksal gruplara ayır.
- Global time range selector ve refresh kontrollerini uygun ekranlarda erişilebilir tut.

**3. Overview Dashboard**

- Kullanıcı platforma girdiğinde sistemin genel sağlık durumunu hızlıca anlayabilsin.
- Overall Risk Score, Active Incidents, Monitored Services, Error Rate ve P95 Latency gibi metrikleri önceliklendir.
- Metrik kartları kompakt ve bilgi odaklı olsun.
- Risk trendlerini zaman serisi grafikleriyle göster.
- Servislerin durumlarını kolay taranabilir tablolarla sun.
- Kritik olaylar görsel hiyerarşide öncelikli olsun.
- Gereksiz başlıklar, açıklamalar ve tekrarlanan bilgiler kaldırılmalı.
- Sadece mevcut backend tarafından sağlanan gerçek verileri kullan.

**4. Risk Score**

- Risk Score 0–100 aralığında net biçimde gösterilsin.
- Skorun hangi metriklerden oluştuğu incelenebilsin.
- Risk geçmişi ve önceki ölçümlerle karşılaştırma sunulsun.
- Risk severity renkleri yalnızca ilgili durumlarda kullanılsın.
- Büyük ve dekoratif gauge grafiklerini gereksiz yere kullanma.
- Mevcut deterministik scoring mekanizmasını değiştirme.

**5. AI Investigation ve Root Cause Analysis**

AI tarafından üretilen analizlerin gösterildiği ekranları bir chatbot çıktısı gibi değil, profesyonel bir incident investigation arayüzü gibi tasarla.

Analiz sonuçlarını aşağıdaki bölümlerde sun:

- Executive Summary
- Root Cause
- Impacted Services
- Supporting Evidence
- Incident Timeline
- Alternative Hypotheses
- Confidence Level
- Recommended Actions
- Data Quality

Uzun metin duvarlarından kaçın. Progressive disclosure, expandable sections ve evidence drill-down kullan.

DQL sorguları ve ham kanıtlar gerektiğinde incelenebilsin. Teknik iddialar ilgili kanıtlarla ilişkilendirilsin.

**6. Charts ve Data Visualization**

- Grafikler dekoratif değil, operasyonel karar vermeyi kolaylaştırıcı olsun.
- Grafiklerde gereksiz renk kullanımını engelle.
- Baseline ile güncel değerler ayırt edilebilsin.
- Tooltip, legend, axis, time range ve unit gösterimleri tutarlı olsun.
- Aynı metriğin rengi farklı ekranlarda değişmesin.
- Yeterli veri bulunmadığında yanıltıcı grafik oluşturma.

**7. User Experience**

- Kullanıcının bir işlemi tamamlaması için gereken tıklama sayısını azalt.
- Loading, skeleton, empty, error ve unavailable state'lerini tasarla.
- Filtreleme ve sıralama özellikleri kolay kullanılabilir olsun.
- Tablolarda uygun olduğunda pagination ve sticky header kullan.
- Responsive tasarım uygula.
- Accessibility ve WCAG AA kontrast prensiplerini gözet.
- Animasyonları kısa, sade ve işlevsel tut.
- Gereksiz modal ve popup kullanımından kaçın.

**8. Technical Terminology**

Teknik İngilizce kavramları yapay biçimde Türkçeleştirme.

Root Cause Analysis, Lock Contention, Thread Pool, Connection Pool, Baseline, Evidence, Throughput, Latency, Incident, Deadlock, Trace ve Span gibi kavramları orijinal halleriyle koru.

**9. Kesinlikle kaçınılması gereken tasarımlar**

- Mor-mavi AI gradient'leri
- Aşırı büyük hero section'lar
- Her yerde rounded card kullanımı
- Gereksiz badge ve pill bileşenleri
- Neon grafikler
- Dekoratif animasyonlar
- Emoji kullanımı
- Aşırı büyük başlıklar
- Gereksiz AI branding
- ChatGPT benzeri rapor ekranları
- Gerçek olmayan metrikler
- Birbirini tekrar eden dashboard bileşenleri
- Gereksiz boşluk nedeniyle düşük bilgi yoğunluğu

**10. Implementation**

Öncelikle mevcut frontend mimarisini, component yapısını, routing mekanizmasını, API entegrasyonlarını ve mevcut ekranları analiz et.

Ardından:

1. Mevcut UI/UX problemlerini belirle.
2. Merkezi design system oluştur.
3. Ortak ve reusable component'leri düzenle.
4. Navigation ve layout yapısını iyileştir.
5. Tüm ekranları tutarlı biçimde yeniden tasarla.
6. Responsive ve accessibility kontrollerini gerçekleştir.
7. Mevcut fonksiyonların çalışmaya devam ettiğini doğrula.
8. Build, lint ve mevcut frontend testlerini çalıştır.

Backend API contract'larını, LangGraph workflow'unu, MCP entegrasyonlarını, deterministik risk hesaplama mekanizmasını ve mevcut iş mantığını değiştirme.

Sadece öneri sunmakla kalma. Gerekli frontend değişikliklerini doğrudan kaynak kodlarına uygula.

Nihai ürün, gerçek bir SRE ekibinin günlük operasyonlarında uzun süre kullanabileceği, sade, güven veren, yüksek bilgi yoğunluğuna sahip profesyonel bir enterprise platform olmalıdır.