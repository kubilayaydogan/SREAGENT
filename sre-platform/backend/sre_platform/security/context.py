"""İstek bağlamı (contextvar): istemci IP'si ve oturum kimliği. Audit kayıtları bu değerleri otomatik alır.

İstemci IP'si: doğrudan bağlanan eş (peer) `SRE_TRUSTED_PROXIES` içindeyse X-Forwarded-For'un EN SAĞDAKİ değeri
(güvenilen proxy'nin eklediği, istemcinin taklit edemeyeceği) kullanılır; aksi halde eş adresi. İstemcinin
gönderdiği X-Forwarded-For başlığı güvenilmeyen eşten geldiğinde yok sayılır.
"""
from __future__ import annotations

import contextvars
import ipaddress

client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar("client_ip", default=None)
session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)


def parse_networks(items) -> tuple:
    out = []
    for item in items:
        try:
            out.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(out)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, trusted: tuple) -> str | None:
    if not peer:
        return None
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer[:64]
    if forwarded_for and any(peer_ip in n for n in trusted):
        last = forwarded_for.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            return str(peer_ip)
    return str(peer_ip)
