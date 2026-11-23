"""Chaos v2 netem sidecar: bir lab uygulamasının network namespace'inde (network_mode: service:<app>) çalışır
ve YALNIZ o container'ın egress trafiğine, YALNIZ allowlist'teki hedeflere tc netem (delay / jitter / loss) uygular.

Güvenlik:
  * Erişim: X-Lab-Token (>= 16 karakter, sabit zamanlı karşılaştırma) + kaynak IP allowlist (chaos controller).
  * Keyfi komut / keyfi hedef YOK: hedef adı (db, reservation, checkin, ...) sunucu tarafındaki allowlist'ten
    IP'ye çözülür; tc komutları liste argümanlarla, shell olmadan çalıştırılır.
  * Değerler sınırlı (delay <= 2000 ms, jitter <= 500 ms, loss <= 50 %); her kural TTL ile otomatik kalkar
    (en fazla 900 s). Başlangıçta ve SIGTERM'de tüm kurallar temizlenir.
  * Container yalnız CAP_NET_ADMIN ile çalışır (compose: cap_drop ALL); etki yalnız bu namespace'tedir.

Uçlar:  GET /netem   POST /netem {"target","delayMs","jitterMs","lossPct","ttlSec"}   DELETE /netem   GET /healthz
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("netem-agent")

MAX_DELAY_MS = 2000
MAX_JITTER_MS = 500
MAX_LOSS_PCT = 50.0
MAX_TTL_S = 900
BAND = "1:4"          # prio qdisc'in 4. bandı netem'e bağlanır; diğer trafik 1..3 bantlarında (etkilenmez)


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int | None      # None → hedef IP'nin tüm portları


@dataclass
class Rule:
    target: str
    ip: str
    port: int | None
    delay_ms: int
    jitter_ms: int
    loss_pct: float
    expires_at: float
    applied_at: float


class ValidationError(ValueError):
    pass


def parse_targets(spec: str) -> dict[str, Target]:
    """NETEM_TARGETS="db=10.0.0.5:5432,reservation=reservation,checkin=checkin" → ad → Target."""
    out: dict[str, Target] = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        name, _, hp = item.partition("=")
        host, _, port = hp.partition(":")
        if not name or not host:
            raise ValueError(f"geçersiz NETEM_TARGETS girdisi: {item!r}")
        out[name.strip()] = Target(name.strip(), host.strip(), int(port) if port else None)
    return out


def validate(body: dict, targets: dict[str, Target]) -> tuple[Target, int, int, float, int]:
    if not isinstance(body, dict):
        raise ValidationError("JSON object bekleniyor")
    name = body.get("target")
    if name not in targets:
        raise ValidationError(f"target allowlist'te değil: {name!r} (izinli: {sorted(targets)})")

    def num(key, lo, hi, default, cast):
        v = body.get(key, default)
        try:
            v = cast(v)
        except (TypeError, ValueError):
            raise ValidationError(f"{key} sayı olmalı") from None
        if not lo <= v <= hi:
            raise ValidationError(f"{key} {lo}..{hi} aralığında olmalı (verilen {v})")
        return v

    delay = num("delayMs", 0, MAX_DELAY_MS, 0, int)
    jitter = num("jitterMs", 0, MAX_JITTER_MS, 0, int)
    loss = num("lossPct", 0.0, MAX_LOSS_PCT, 0.0, float)
    ttl = num("ttlSec", 5, MAX_TTL_S, 120, int)
    if delay == 0 and loss == 0:
        raise ValidationError("delayMs veya lossPct > 0 olmalı")
    if jitter > delay:
        raise ValidationError("jitterMs delayMs'den büyük olamaz")
    return targets[name], delay, jitter, loss, ttl


def netem_args(delay_ms: int, jitter_ms: int, loss_pct: float) -> list[str]:
    args: list[str] = []
    if delay_ms > 0:
        args += ["delay", f"{delay_ms}ms"]
        if jitter_ms > 0:
            args += [f"{jitter_ms}ms", "distribution", "normal"]
    if loss_pct > 0:
        args += ["loss", f"{loss_pct:g}%"]
    return args


def build_apply(dev: str, rules: list[Rule]) -> list[list[str]]:
    """Tüm kural setini sıfırdan kuran tc komutları. Her hedef kendi netem sınıfına (1:4, 1:5 ...) gider.
    prio qdisc bands = 3 + kural sayısı; varsayılan priomap 0..2 bantlarını kullanır → etiketsiz trafik etkilenmez."""
    cmds = [["tc", "qdisc", "del", "dev", dev, "root"]]
    if not rules:
        return cmds
    bands = 3 + len(rules)
    cmds.append(["tc", "qdisc", "add", "dev", dev, "root", "handle", "1:", "prio", "bands", str(bands),
                 "priomap", "1", "2", "2", "2", "1", "2", "0", "0", "1", "1", "1", "1", "1", "1", "1", "1"])
    for i, r in enumerate(rules):
        band = 4 + i
        cmds.append(["tc", "qdisc", "add", "dev", dev, "parent", f"1:{band}", "handle", f"{40 + i}:", "netem",
                     *netem_args(r.delay_ms, r.jitter_ms, r.loss_pct)])
        match = ["match", "ip", "dst", f"{r.ip}/32"]
        if r.port:
            match += ["match", "ip", "dport", str(r.port), "0xffff"]
        cmds.append(["tc", "filter", "add", "dev", dev, "parent", "1:0", "protocol", "ip", "prio", "1", "u32",
                     *match, "flowid", f"1:{band}"])
    return cmds


def default_dev() -> str:
    try:
        out = subprocess.run(["ip", "-o", "route", "show", "default"], capture_output=True, text=True,
                             check=True, timeout=5).stdout.split()
        return out[out.index("dev") + 1]
    except Exception:  # noqa: BLE001
        return "eth0"


class Agent:
    def __init__(self, targets: dict[str, Target], dev: str, runner=None, clock=time.time,
                 resolver=socket.gethostbyname):
        self.targets = targets
        self.dev = dev
        self.run = runner or self._run
        self.clock = clock
        self.resolve = resolver
        self.rules: dict[str, Rule] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _run(cmd: list[str]) -> None:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        # "qdisc del" kural yokken hata verir; zararsız
        if res.returncode != 0 and cmd[1:3] != ["qdisc", "del"]:
            raise RuntimeError(f"{' '.join(cmd)} → {res.stderr.strip()}")

    def _sync(self) -> None:
        for cmd in build_apply(self.dev, list(self.rules.values())):
            self.run(cmd)

    def apply(self, body: dict) -> Rule:
        t, delay, jitter, loss, ttl = validate(body, self.targets)
        ip = self.resolve(t.host)
        ipaddress.ip_address(ip)          # çözümleme sonucu gerçekten IP mi
        now = self.clock()
        rule = Rule(t.name, ip, t.port, delay, jitter, loss, now + ttl, now)
        with self.lock:
            self.rules[t.name] = rule
            try:
                self._sync()
            except Exception:
                self.rules.pop(t.name, None)
                self._sync()
                raise
        LOG.info("netem uygulandı target=%s ip=%s port=%s delay=%sms jitter=%sms loss=%s%% ttl=%ss",
                 t.name, ip, t.port, delay, jitter, loss, ttl)
        return rule

    def clear(self, target: str | None = None) -> int:
        with self.lock:
            n = len(self.rules) if target is None else int(target in self.rules)
            if target is None:
                self.rules.clear()
            else:
                self.rules.pop(target, None)
            self._sync()
        LOG.info("netem temizlendi target=%s removed=%d", target or "*", n)
        return n

    def expire(self) -> int:
        now = self.clock()
        with self.lock:
            dead = [k for k, r in self.rules.items() if r.expires_at <= now]
            for k in dead:
                del self.rules[k]
            if dead:
                self._sync()
        if dead:
            LOG.info("netem TTL doldu: %s", dead)
        return len(dead)

    def status(self) -> dict:
        now = self.clock()
        with self.lock:
            return {"dev": self.dev, "targets": sorted(self.targets),
                    "rules": [{**asdict(r), "remainingSec": max(0, round(r.expires_at - now))}
                              for r in self.rules.values()]}


def make_handler(agent: Agent, token: str, allowed: list[ipaddress._BaseNetwork]):
    class H(BaseHTTPRequestHandler):
        server_version = "netem-agent"

        def log_message(self, fmt, *args):  # noqa: N802
            LOG.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, obj) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            try:
                src = ipaddress.ip_address(self.client_address[0])
            except ValueError:
                return False
            if not any(src in n for n in allowed):
                self._send(403, {"error": "kaynak IP allowlist'te değil"})
                return False
            got = self.headers.get("X-Lab-Token", "")
            if not hmac.compare_digest(got.encode(), token.encode()):
                self._send(401, {"error": "geçersiz lab token"})
                return False
            return True

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if not self._authorized():
                return None
            if self.path == "/netem":
                return self._send(200, agent.status())
            return self._send(404, {"error": "yok"})

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                return None
            if self.path != "/netem":
                return self._send(404, {"error": "yok"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4096:
                return self._send(413, {"error": "gövde çok büyük"})
            try:
                rule = agent.apply(json.loads(self.rfile.read(n) or b"{}"))
            except (ValidationError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                LOG.exception("netem uygulanamadı")
                return self._send(500, {"error": str(e)})
            return self._send(200, asdict(rule))

        def do_DELETE(self):  # noqa: N802
            if not self._authorized():
                return None
            if self.path.startswith("/netem"):
                target = self.path.partition("?target=")[2] or None
                return self._send(200, {"removed": agent.clear(target)})
            return self._send(404, {"error": "yok"})

    return H


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format='{"@timestamp":"%(asctime)s","log.level":"%(levelname)s","message":"%(message)s",'
                               '"service.name":"netem-agent-' + os.environ.get("NETEM_APP", "?") + '"}')
    token = os.environ.get("LAB_TOKEN", "")
    if len(token) < 16:
        LOG.error("LAB_TOKEN en az 16 karakter olmalı")
        return 2
    targets = parse_targets(os.environ.get("NETEM_TARGETS", ""))
    allowed = [ipaddress.ip_network(c.strip()) for c in os.environ.get("NETEM_ALLOWED_CIDRS", "172.28.0.50/32").split(",")
               if c.strip()]
    agent = Agent(targets, os.environ.get("NETEM_DEV") or default_dev())
    agent.clear()                      # önceki çalışmadan kalan kural olmasın

    stop = threading.Event()

    def janitor():
        while not stop.wait(1.0):
            try:
                agent.expire()
            except Exception:  # noqa: BLE001
                LOG.exception("TTL temizliği başarısız")

    threading.Thread(target=janitor, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("NETEM_PORT", "9190"))),
                              make_handler(agent, token, allowed))

    def shutdown(*_):
        stop.set()
        try:
            agent.clear()
        finally:
            threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    LOG.info("netem-agent hazır dev=%s targets=%s", agent.dev, sorted(targets))
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
