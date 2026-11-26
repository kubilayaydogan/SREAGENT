"""Streaming çıktı koruması.

Token'lar kullanıcıya akarken hassas veri doğrulaması tamamlanmadan iletilmemelidir. `StreamGuard`:
* gelen parçaları bir tampona alır; kelime sınırına kadar olan kısmı final_output aşamasının sensitive_data
  policy'leriyle maskeleyip iletir. Tamamlanmamış son kelime, boşluk içerebilen aday kalıplar (Authorization / Bearer,
  password: …, kart / IBAN grupları) ve kapanmamış private key bloğu TUTULUR: parça sınırına bölünen secret da
  yakalanır, akış yalnız son kelime kadar bekler;
* `flush()` akış bitince kalan kısmı aynı kurallarla iletir ve tek bir değerlendirme kaydı üretir.

Bu yalnız akış-güvenli (yerel) kurallar içindir. Evidence / risk tutarlılığı gibi tüm metni gerektiren policy'ler akış
bitince `final_output` ile değerlendirilir; düzeltme notları kalıcı yanıta (ve `answer_replaced` olayına) eklenir.
"""
from __future__ import annotations

import re
import time
from dataclasses import replace

from . import detect
from .model import EvalContext, Evaluation, Finding, new_id
from .runtime import RUN, fallback_engine

_PK = "-----BEGIN"
# Boşluk içerebilen hassas kalıpların başlangıcı (kesimden önce tutulur): header / anahtar-değer / kart & IBAN grupları
_SPACED = re.compile(r"(?i:\b(?:authorization|proxy-authorization|bearer|basic|password|passwd|pwd|secret|token|"
                     r"api[_-]?key|x-api-key|cookie|set-cookie)\b[^\n]{0,80}$)|(?:\b[A-Z]{2}\d{2}[ \dA-Z]{4,40}$)|"
                     r"(?:\b\d{4}[ -][\d -]{0,30}$)")


class StreamGuard:
    def __init__(self, hold: int = 256):
        run = RUN.get()
        self.run = run
        engine = run.engine if run is not None else fallback_engine()
        ctx = run.ctx if run is not None else EvalContext(run_kind="unbound", user_role="system")
        self.ctx = replace(ctx, stage="final_output")
        self.policies = [p for p in engine.applicable(("final_output",), self.ctx)
                         if p.type == "sensitive_data" and p.effective_mode() == "enforce" and p.action == "redact"]
        self.monitor = [p for p in engine.applicable(("final_output",), self.ctx)
                        if p.type == "sensitive_data" and p.effective_mode() == "monitor"]
        self.hold = hold
        self.buf = ""
        self.hits: list[dict] = []
        self.ms = 0.0
        self.chunks = 0

    def _mask(self, text: str, final: bool) -> str:
        t0 = time.perf_counter()
        for p in self.policies + self.monitor:
            types = (p.params or {}).get("types")
            hits, red, _ = detect.scan(text, types, (p.params or {}).get("custom_patterns"))
            if hits:
                self.hits.extend({"policy": p.key, "type": h["type"], "enforced": p in self.policies} for h in hits)
                if p in self.policies:
                    text = red
        self.ms += (time.perf_counter() - t0) * 1000
        return text

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self.chunks += 1
        if not self.policies and not self.monitor:
            return chunk
        self.buf += chunk
        cut = self._cut()
        if cut <= 0:
            return ""
        out, self.buf = self.buf[:cut], self.buf[cut:]
        return self._mask(out, False)

    def _cut(self) -> int:
        """İletilebilecek güvenli önek uzunluğu. Secret'lar boşluk içermez → kesim kelime sınırında yapılır; boşluklu
        aday kalıplar (Authorization / Bearer / password: …, kart / IBAN grupları) ve PEM bloğu tamamlanana kadar
        tutulur. Boşluksuz çok uzun parça `hold` karakterinin 4 katını aşınca maskelenerek iletilir."""
        b = self.buf
        pk = b.rfind(_PK)
        if pk != -1 and "-----END" not in b[pk:]:
            return pk
        ws = max(b.rfind(" "), b.rfind("\n"), b.rfind("\t"))
        if ws == -1:
            return len(b) - self.hold if len(b) > self.hold * 4 else 0
        cut = ws + 1
        tail = b[max(0, cut - 120):cut]
        m = _SPACED.search(tail)
        if m:
            cut = max(0, cut - len(tail) + m.start())
        return cut

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        res = self._mask(out, True) if out and (self.policies or self.monitor) else out
        self._record()
        return res

    def _record(self) -> None:
        run = self.run
        if run is None or run.sink is None:
            return
        for p in self.policies + self.monitor:
            mine = [h for h in self.hits if h["policy"] == p.key]
            f = Finding(bool(mine), f"akış içinde hassas veri: {', '.join(sorted({h['type'] for h in mine}))}"
                        if mine else "", "sensitive_stream" if mine else None,
                        matches=[{"type": h["type"]} for h in mine][:50], changed=["$.stream"] if mine and
                        p in self.policies else [])
            ev = Evaluation(new_id(), p, "final_output", "redacted" if mine else "passed",
                            enforced=p in self.policies, finding=f, duration_ms=self.ms)
            run.record(ev, self.ctx, {"stream": {"chunks": self.chunks}}, {"stream": {"chunks": self.chunks}})
