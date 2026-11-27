"""Merkezi terminology policy: açıklama dili Türkçe, teknik terimler İngilizce.

Tek kaynak `terminology.yaml`dır. Buradan üç şey üretilir:
  * `POLICY_PROMPT`  — kullanıcıya metin üreten HER system prompt'a eklenen kural bloğu (`with_policy`).
  * `find_violations` — bir metindeki kelime kelime Türkçe çevirileri (calque) bulur. Otomatik testler deterministik
    rapor metinlerini, UI metinlerini ve prompt'ları bununla denetler; LLM çıktısında yalnız log/telemetri için
    kullanılır (model çıktısı hiçbir zaman otomatik değiştirilmez: log, metric, DQL, entity adı gibi değerler bozulmasın).
  * `glossary()`     — UI/doküman için alan bazlı terim listesi.
  * `find_internal` / `reader_lines` — uygulamanın iç adlarının (mechanism, window, source, signal …) okur diline
    çevrilmesi: sektör terimi olmayan bu adlar metne taşınmaz (bkz. `reader_terms`, style.STYLE_PROMPT).

Policy sabit listeye dayanmaz: prompt, listelenen alanlardaki TÜM standart terimlerin (listede olmasa da)
İngilizce korunmasını ister; liste yalnızca örnek ve test kapsamıdır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml

# Kod/veri bölgeleri taranmaz: `inline code`, ``` blokları, <..._data> blokları, URL'ler, tırnak içi log alıntıları değil
# (onlar da backtick ile verilmeli). Bunlar değiştirilmemesi gereken değerlerdir; violation sayılmaz.
_CODE = re.compile(r"```.*?```|`[^`\n]*`|<(\w+_data)>.*?</\1>|https?://\S+", re.S)
_TR_WORD = "a-zA-ZçğıöşüÇĞİÖŞÜâîû"


@dataclass(frozen=True)
class Calque:
    pattern: re.Pattern
    term: str


@dataclass(frozen=True)
class Violation:
    text: str
    term: str
    start: int

    def __str__(self) -> str:
        return f"'{self.text}' → {self.term}"


@lru_cache(maxsize=1)
def _data() -> dict:
    return yaml.safe_load(resources.files(__package__).joinpath("terminology.yaml").read_text(encoding="utf-8"))


def domains() -> dict[str, list[str]]:
    return dict(_data()["domains"])


@lru_cache(maxsize=1)
def calques() -> tuple[Calque, ...]:
    out = []
    for c in _data()["calques"]:
        # Kelime başında eşleş (Türkçe harfler dahil); ek'leri pattern içindeki \w* yakalar.
        out.append(Calque(re.compile(rf"(?<![{_TR_WORD}])(?:{c['pattern']})", re.I | re.U), c["term"]))
    return tuple(out)


def _tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def find_violations(text: str | None) -> list[Violation]:
    """Kelime kelime çevrilmiş teknik terimleri döndürür (kod/veri bölgeleri hariç). Çakışan eşleşmelerde ilk
    (daha özel) kalıp kazanır; yaml'daki sıra bu yüzden özelden genele gider."""
    if not text:
        return []
    masked = _CODE.sub(lambda m: " " * len(m.group(0)), text)
    low = _tr_lower(masked)
    taken: list[tuple[int, int]] = []
    found: list[Violation] = []
    for c in calques():
        for m in c.pattern.finditer(low):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append(Violation(text[m.start():m.end()], c.term, m.start()))
    return sorted(found, key=lambda v: v.start)


@dataclass(frozen=True)
class ReaderTerm:
    pattern: re.Pattern
    internal: str
    say: str


@lru_cache(maxsize=1)
def reader_terms() -> tuple[ReaderTerm, ...]:
    """Uygulamanın iç adları ve okura yazılacak karşılıkları (terminology.yaml `reader_terms`)."""
    return tuple(ReaderTerm(re.compile(rf"(?<![{_TR_WORD}])(?:{t['pattern']})", re.I | re.U), t["internal"], t["say"])
                 for t in _data().get("reader_terms") or [])


def find_internal(text: str | None) -> list[Violation]:
    """Metne sızan iç adları döndürür (kod / veri bölgeleri hariç). `term` alanı okura yazılacak karşılıktır.
    Deterministik metinler testlerde bununla denetlenir; LLM çıktısında yalnız log'lanır, metin değiştirilmez."""
    if not text:
        return []
    masked = _CODE.sub(lambda m: " " * len(m.group(0)), text)
    low = _tr_lower(masked)
    found = [Violation(text[m.start():m.end()], t.say, m.start())
             for t in reader_terms() for m in t.pattern.finditer(low)]
    return sorted(found, key=lambda v: v.start)


def reader_lines() -> str:
    """Prompt için "iç ad → okura yazılacak karşılık" satırları."""
    return "\n".join(f"  · {t.internal} → {t.say}" for t in reader_terms())


def glossary() -> dict:
    return {"domains": domains(), "calques": [{"pattern": c.pattern.pattern, "term": c.term} for c in calques()],
            "reader_terms": [{"internal": t.internal, "say": t.say} for t in reader_terms()]}


def _examples() -> str:
    seen, rows = set(), []
    for c in _data()["calques"]:
        if c["term"] in seen:
            continue
        seen.add(c["term"])
        tr = c.get("tr") or c["pattern"].split("|")[0].replace(r"\w*", "")
        rows.append(f'"{tr}" değil {c["term"]}')
    return "; ".join(rows)


def _domain_lines() -> str:
    return "\n".join(f"  · {name}: {', '.join(terms)} …" for name, terms in domains().items())


POLICY_PROMPT = f"""
DİL VE TERMINOLOGY POLICY (tüm çıktılar için geçerli; diğer kurallarla çelişirse bu kural geçerlidir):
- Açıklama ve analiz dili Türkçedir; cümle yapısı, bağlaçlar ve yorumlar Türkçe kalır.
- SRE, DevOps, Linux, Kubernetes, PostgreSQL, JVM, Kafka, Dynatrace, Observability, Networking, Cloud ve
  Distributed Systems alanlarının standart teknik terimleri İngilizce yazılır. Aşağıdaki liste ÖRNEKTİR; listede
  olmayan ama sektörde İngilizce kullanılan her standart terimi de (ör. herhangi bir metric, component, pattern
  veya hata türü adı) İngilizce koru:
{_domain_lines()}
- Teknik terimleri kelime kelime Türkçeye ÇEVİRME. Örnekler: {_examples()}.
- Bir terimin açıklanması gerekiyorsa İngilizce terimi koru, Türkçe açıklamayı parantez içinde ver:
  "Consumer Lag (consumer'ın topic'teki son mesajın gerisinde kalma miktarı)". Terimin kendisini değiştirme.
- Türkçe ekleri kesme işaretiyle ekle: lock'lar, transaction'ın, Connection Pool'u, query'ler, span'lerde.
- Şu değerleri ASLA değiştirme, çevirme veya yeniden biçimlendirme; veride nasılsa öyle aktar: log satırları,
  metric ve span adları, trace/span ID'leri, exception sınıfları ve mesajları, API field adları, SQL keyword'leri
  ve SQL metni, DQL expression'ları, entity/servis/uygulama adları, error code'ları, HTTP status'ları, kayıt ve
  evidence kimlikleri (ev-…, inv:…, rca:…), dosya yolları ve konfigürasyon anahtarları. Bunları mümkünse
  `backtick` içinde ver.
- Doğru örnek: "PostgreSQL üzerinde Lock Wait oluşmuş. Bazı transaction'lar diğer transaction'ların tuttuğu
  lock'ların serbest bırakılmasını bekliyor. Bu durum Query Latency değerlerini artırıyor."
  Yanlış örnek: "Veritabanındaki kilit kenarları nedeniyle iş parçacıkları bekliyor."
"""


def with_policy(system_prompt: str) -> str:
    """Kullanıcıya görünen metin üreten bir system prompt'a ortak yazım üslubunu (style.STYLE_PROMPT) ve policy
    bloğunu ekler (idempotent). Sıra: prompt'un kendi talimatları → üslup → terminology policy (son söz policy'de)."""
    if "TERMINOLOGY POLICY" in system_prompt:
        return system_prompt
    from .style import STYLE_MARK, STYLE_PROMPT
    body = system_prompt.rstrip()
    if STYLE_MARK not in body:
        body += "\n" + STYLE_PROMPT.rstrip()
    return body + "\n" + POLICY_PROMPT


# ---- Kaynak kodu lint'i (testler kullanır): kullanıcıya görünen string'lerdeki calque'ları bulur ---------------------
# Bilerek Türkçe karşılık içeren satırlar (ör. kullanıcı GİRDİSİNİ sınıflandıran eş anlamlı listeleri) satır sonuna
# `# term-ok` / `// term-ok` yorumu ile işaretlenir; bu satırlar kullanıcıya gösterilmez.
ALLOW_MARK = "term-ok"

def scan_python_source(src: str) -> list[tuple[int, str, Violation]]:
    """Python kaynağındaki string literal'leri (docstring ve yorumlar hariç) tarar → (satır, string, violation)."""
    import ast

    tree = ast.parse(src)
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                doc_nodes.add(id(first.value))
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            if any(ALLOW_MARK in ln for ln in lines[node.lineno - 1:(node.end_lineno or node.lineno)]):
                continue
            for v in find_violations(node.value):
                out.append((node.lineno, node.value, v))
    return out


_TS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'`])//[^\n]*|\{/\*.*?\*/\}", re.S)
_TS_TEXT = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`|>([^<>{}]+)<", re.S)


def scan_ts_source(src: str) -> list[tuple[int, str, Violation]]:
    """TS/TSX kaynağındaki string literal'leri ve JSX metnini (yorumlar hariç) tarar."""
    clean = _TS_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), src)
    lines = src.splitlines()
    out = []
    for m in _TS_TEXT.finditer(clean):
        s = m.group(1) if m.group(1) is not None else m.group(0)[1:-1]
        first = clean.count("\n", 0, m.start())
        if any(ALLOW_MARK in x for x in lines[first:first + 1 + m.group(0).count("\n")]):
            continue
        for v in find_violations(s):
            out.append((clean.count("\n", 0, m.start()) + 1, s.strip(), v))
    return out
