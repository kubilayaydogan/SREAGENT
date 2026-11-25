"""Giden bağlantılar için SSRF koruması (connector testi, LLM base_url, Slack webhook).

Kurallar:
  * yalnız https; http yalnız deployment allowlist'indeki host'lar için
  * URL'de kullanıcı adı/parola yasak
  * host'un TÜM DNS adresleri global (public) olmalı; private, loopback, link-local (169.254.0.0/16 metadata dahil),
    multicast, reserved adresler reddedilir — deployment allowlist'i (SRE_OUTBOUND_ALLOWLIST) hariç
  * redirect takip edilmez (MCP/httpx istemcileri varsayılan follow_redirects=False), TLS doğrulaması açık
  * cevap boyutları çağıranlarda sınırlandırılır

SRE_OUTBOUND_ALLOWLIST: virgülle ayrılmış host (ör. mcp.internal), joker son ek (*.corp.local) veya CIDR (10.0.0.0/8).
Allowlist deployment'a aittir; arayüzden değiştirilemez. DNS rebinding'e karşı kalan risk: kontrol ile bağlantı
arasında adres değişirse; bunun için allowlist'i dar tutun.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


class UnsafeUrl(ValueError):
    pass


def _allowlist() -> tuple[set[str], list[str], list[ipaddress._BaseNetwork]]:
    hosts, suffixes, nets = set(), [], []
    for item in (x.strip().lower() for x in os.getenv("SRE_OUTBOUND_ALLOWLIST", "").split(",")):
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
            continue
        except ValueError:
            pass
        if item.startswith("*."):
            suffixes.append(item[1:])
        else:
            hosts.add(item)
    return hosts, suffixes, nets


def allowlist_view() -> list[str]:
    return [x.strip() for x in os.getenv("SRE_OUTBOUND_ALLOWLIST", "").split(",") if x.strip()]


def _host_allowed(host: str, hosts: set[str], suffixes: list[str]) -> bool:
    return host in hosts or any(host.endswith(s) for s in suffixes)


def _resolve(host: str, port: int) -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
    except socket.gaierror as e:
        raise UnsafeUrl(f"DNS çözümlenemedi: {host} ({e.strerror or e})") from None


def check_url(url: str, *, resolver=None) -> str:
    """Güvenli değilse UnsafeUrl. Normalize URL döndürür."""
    resolver = resolver or _resolve
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise UnsafeUrl("URL ayrıştırılamadı") from None
    if parts.scheme not in ("https", "http"):
        raise UnsafeUrl("yalnız https (veya allowlist'teki host için http) desteklenir")
    if parts.username or parts.password:
        raise UnsafeUrl("URL içinde kullanıcı bilgisi olamaz; secret referansı kullanın")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeUrl("host yok")
    hosts, suffixes, nets = _allowlist()
    host_ok = _host_allowed(host, hosts, suffixes)
    if parts.scheme == "http" and not host_ok:
        raise UnsafeUrl("şifresiz http yalnız SRE_OUTBOUND_ALLOWLIST'teki host'lar için kullanılabilir")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    for addr in resolver(host, port):
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
        if any(ip in n for n in nets) or host_ok:
            continue
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrl(f"{host} iç/özel ağ adresine çözümleniyor ({ip}); gerekiyorsa deployment "
                            f"SRE_OUTBOUND_ALLOWLIST'e eklenmeli")
    return parts.geturl()


async def check_runtime(url: str) -> str:
    """Çalışma anında (her bağlantı kurulmadan önce) aynı kural; kayıt sonrası DNS değişimini (rebinding) yakalar.
    Bloklayan DNS çözümü thread'de yapılır. Güvenli değilse UnsafeUrl."""
    import asyncio
    return await asyncio.to_thread(check_url, url)
