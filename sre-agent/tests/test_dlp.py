"""DLP (P1.8): secret kalıpları her sink'te, PII checksum doğrulamalı ve sink politikasına göre maskelenir."""
from __future__ import annotations

import pytest

from airlab_agent import redact as R


# Fixture'lar parçalı yazılır: kaynakta tam secret kalıbı bulunmaz (gitleaks), çalışma anında birleşir.
_J = ".".join(["eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiJ0ZXN0LWZpeHR1cmUifQ", "c2lnbmF0dXJlLXRlc3Qtb25seQ"])
_PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\nTESTONLY\n-----END RSA " + "PRIVATE KEY-----"


@pytest.mark.parametrize("raw,leak", [
    ("token " + _J, "zdWIiOiJ0ZXN0"),
    ("Authorization: " + "Bearer " + "testonly" + "abcdefgh12345678", "abcdefgh12345678"),
    ("curl -H '" + "bearer " + "testonly" + "qrstuvwxyz'", "qrstuvwxyz"),
    ("post https://hooks.slack.com/" + "services/T000/B000/" + "testonly", "testonly"),
    (_PEM, "TESTONLY"),
    ("aws " + "AKIA" + "TESTONLYEXAMPLE0", "TESTONLYEXAMPLE0"),
])
def test_secrets_masked_on_every_sink(raw, leak):
    for sink in R.SINKS + ("bilinmeyen",):
        assert leak not in R.redact_text(raw, sink), sink


@pytest.mark.parametrize("raw,tag", [
    ("iletişim: ayse.yilmaz+test@ornek.com.tr", "[email]"),
    ("IBAN TR33 0006 1005 1978 6457 8413 26", "[iban]"),
    ("iban=GB82WEST12345698765432", "[iban]"),
    ("kart 4111 1111 1111 1111 ile", "[card]"),
    ("kart 5500-0000-0000-0004", "[card]"),
    ("tckn 10000000146", "[tckn]"),
])
def test_pii_masked_by_default(raw, tag):
    assert tag in R.redact_text(raw)
    assert tag in R.redact_text(raw, "llm")


@pytest.mark.parametrize("raw", [
    "ts=1764072000000 duration_ns=7111111111111111",          # zaman damgası; geçerli IIN'i olmayan sayı
    "trace 4111111111111112",                                # Luhn başarısız
    "TR33 0006 1005 1978 6457 8413 27",                      # IBAN checksum başarısız
    "id 10000000147",                                        # TCKN checksum başarısız
    "host 10.0.0.12 port 5432 pid 1234567",
    "dt.host.cpu.usage avg=12.5 count=12345678901",          # 11 hane ama TCKN değil
    "version 1.2.3-4111111111111111",                        # sayı başka bir token'ın parçası
    "kullanıcı@ornek",                                       # TLD yok
])
def test_no_false_positives_on_telemetry_like_values(raw):
    assert R.redact_text(raw, "llm") == raw


def test_nested_structures_and_sink_override(monkeypatch):
    obj = {"msg": "mail ali@ornek.com", "rows": [{"iban": "GB82WEST12345698765432"}], "n": 5}
    assert R.redact(obj, "llm") == {"msg": "mail [email]", "rows": [{"iban": "[iban]"}], "n": 5}
    monkeypatch.setattr(R, "_PII_KEEP", frozenset({"storage"}))
    assert R.redact_text("ali@ornek.com", "storage") == "ali@ornek.com"          # açıkça izin verilen sink
    assert R.redact_text("ali@ornek.com", "llm") == "[email]"
    assert R.redact_text("token=" + "abcd1234efgh", "storage") == "token=***"          # secret her zaman
    assert R.dlp_policy()["storage"] == {"secrets": True, "pii": False}
    assert R.dlp_policy()["llm"] == {"secrets": True, "pii": True}


def test_keep_sinks_env_parsing():
    assert R._keep_sinks("storage, yok ,llm") == {"storage", "llm"}                # bilinmeyen sink yok sayılır
    assert R._keep_sinks("") == frozenset()
