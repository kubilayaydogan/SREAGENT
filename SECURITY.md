# Security Policy

## Güvenlik açığı bildirimi

Bu depo bir lab / iç platform projesidir. Güvenlik açığı bulduğunuzu düşünüyorsanız:

- **Public issue açmayın.** GitHub'ın "Report a vulnerability" (private security advisory) özelliğini kullanın
  ya da depo sahibine doğrudan bildirin.
- Etkilenen bileşeni (frontend, backend API, agent, MCP connector, deployment), yeniden üretme adımlarını ve
  etkisini yazın. Gerçek credential, telemetri ya da müşteri verisi paylaşmayın.
- İlk yanıt hedefi: 5 iş günü.

## Desteklenen sürüm

Yalnız `main` dalı desteklenir.

## Güvenlik mimarisi ve durum

- Gereksinimler: [docs/security/SECURITY_IMPLEMENTATION.md](docs/security/SECURITY_IMPLEMENTATION.md)
- Gap analizi: [docs/security/SECURITY_GAP_ANALYSIS.md](docs/security/SECURITY_GAP_ANALYSIS.md)
- Uygulama planı ve kararlar: [docs/security/SECURITY_IMPLEMENTATION_PLAN.md](docs/security/SECURITY_IMPLEMENTATION_PLAN.md)
- P0 uygulama kanıtı: [docs/security/SECURITY_P0_EVIDENCE.md](docs/security/SECURITY_P0_EVIDENCE.md)

## Secret'lar

Secret'lar repoya girmez (`.gitignore`: `.env`, `app-vm/*.env`, `deploy/out/`). CI her push'ta tüm git geçmişini
gitleaks ile tarar ([.gitleaks.toml](.gitleaks.toml)). Bir secret yanlışlıkla commit edilirse: önce sağlayıcıda
iptal edin / döndürün, sonra geçmişi temizleyin — geçmiş temizliği tek başına yeterli değildir.
