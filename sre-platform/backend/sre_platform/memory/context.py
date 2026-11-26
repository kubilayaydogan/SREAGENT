"""Memory Context Builder: retrieval sonucunun LLM isteğine eklenecek bölümünü bütçeye göre hazırlar ve
Context Injection Inspector için ne eklendiğini kaydeder.

* Token sayısı TAHMİNDİR (karakter / 4); sağlayıcı tokenizer'ı çağrılmaz (dış servise veri gönderilmez). Arayüzde
  "estimate" olarak gösterilir.
* Bütçe (`max_context_tokens`) ve kayıt sınırı (`max_retrieved_records`) 0 iken (varsayılan) içerik DEĞİŞTİRİLMEZ —
  mevcut davranış korunur. Aşılırsa kayıtlar listelerin sonundan (en düşük sıradaki) çıkarılır.
* Kayıt kimlikleri `ref` alanından okunur (inv:…, inc:…, rca:…, rb:…, ki:…, mem:…).
"""
from __future__ import annotations

import copy
import json
from typing import Any

LIST_KEYS = ("episodes", "investigations", "incidents", "rca", "runbooks", "known_issues", "semantic",
             "conversations")
TYPE_OF = {"episodes": "episodic", "investigations": "episodic", "incidents": "episodic", "rca": "episodic",
           "runbooks": "procedural", "known_issues": "semantic", "semantic": "semantic",
           "conversations": "short_term", "last_investigation": "episodic", "app_memory": "episodic"}


def estimate_tokens(obj: Any) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1, default=str)
    return (len(s) + 3) // 4


def refs(mem: dict | None) -> list[dict]:
    out = []
    for k in LIST_KEYS:
        for i, it in enumerate((mem or {}).get(k) or []):
            if isinstance(it, dict):
                out.append({"ref": it.get("ref") or it.get("conversation") or f"{k}[{i}]", "source": k,
                            "memory_type": TYPE_OF.get(k), "title": it.get("title") or it.get("summary")
                            or it.get("excerpt"), "similarity": it.get("similarity")})
    li = (mem or {}).get("last_investigation")
    if isinstance(li, dict):
        out.append({"ref": li.get("ref"), "source": "last_investigation", "memory_type": "episodic",
                    "title": li.get("title"), "similarity": None})
    for app, items in ((mem or {}).get("app_memory") or {}).items():
        for it in items:
            out.append({"ref": it.get("ref"), "source": f"app_memory:{app}", "memory_type": "episodic",
                        "title": it.get("title"), "similarity": None})
    return out


def apply_budget(block: dict | None, *, max_records: int = 0, max_tokens: int = 0) -> tuple[dict | None, list[dict]]:
    """(kırpılmış blok, kırpılan kayıtlar). Sınır yoksa blok aynen döner."""
    if not block or (not max_records and not max_tokens):
        return block, []
    b = copy.deepcopy(block)
    dropped: list[dict] = []
    order = [k for k in LIST_KEYS if b.get(k)]

    def total() -> int:
        return sum(len(b.get(k) or []) for k in LIST_KEYS)

    def over() -> bool:
        return (max_records and total() > max_records) or (max_tokens and estimate_tokens(b) > max_tokens)

    while over():
        # en uzun listenin son öğesi (en düşük sıra) çıkarılır
        cand = [k for k in order if b.get(k)]
        if not cand:
            break
        k = max(cand, key=lambda x: len(b[x]))
        it = b[k].pop()
        dropped.append({"ref": it.get("ref") if isinstance(it, dict) else None, "source": k,
                        "reason": "max_retrieved_records" if max_records and total() + 1 > max_records
                        else "max_context_tokens"})
        if not b[k]:
            b.pop(k)
    return b, dropped
