"""Agent Builder: görsel workflow tanımı, doğrulama, sürümleme ve çalıştırma (LangGraph).

* `definition` — tanım şeması (taslak ve yayımlanan sürüm aynı biçimdedir; secret içermez).
* `validate`   — backend doğrulaması (kaydetme, test, yayımlama, import); hatalar ilgili node / alanla döner.
* `engine`     — tanımı LangGraph StateGraph'a derler ve çalıştırır (mevcut middleware / trace / context window).
* `service`    — CRUD, sürümler, varsayılan agent, schedule bağımlılıkları, run kayıtları.
* `package`    — JSON içe / dışa aktarma.
"""
