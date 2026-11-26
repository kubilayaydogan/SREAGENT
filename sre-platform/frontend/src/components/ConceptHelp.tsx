import { Link } from "react-router";

/** Agent kavramlarının kısa, sade anlatımı. Teknik ayrıntı varsayılan olarak kapalıdır; kullanıcı isterse açar.
 *  Metinler ürünün gerçek davranışını anlatır: her "nerede görürüm" bağlantısı gerçek bir sayfaya gider. */
export type ConceptKey = "context" | "memory" | "middleware" | "guardrails" | "trace" | "mcp";

interface Concept {
  what: string;
  why: string;
  terms: [string, string][];
  links: { label: string; to: string }[];
}

export const CONCEPTS: Record<ConceptKey, Concept> = {
  context: {
    what: "Context window, modelin tek bir çağrıda okuyabildiği metin miktarıdır. Soru, talimatlar, geçmiş mesajlar ve Dynatrace'ten gelen veri bu alanı paylaşır.",
    why: "Alan dolarsa eski içerik kısaltılır ya da çağrı reddedilir. Bir yanıt eksik ya da kesik görünüyorsa önce buraya bakın.",
    terms: [
      ["Token", "Modelin metni ölçtüğü birim; kabaca bir kelimenin parçası."],
      ["Kapasite", "Modelin bir çağrıda kabul ettiği en fazla girdi token'ı."],
      ["Çıktı bütçesi", "Yanıt için ayrılan token; girdi alanından düşülür."],
    ],
    links: [{ label: "Bir çalıştırmanın LLM çağrılarını aç (Agent Trace)", to: "/agent-trace" }],
  },
  memory: {
    what: "Memory, agent'ın önceki konuşmalardan ve çalıştırmalardan hatırladığı bilgidir. Güncel ölçümün yerine geçmez: geçmiş kayıt evidence sayılmaz, risk score'u değiştirmez.",
    why: "Agent bir yanıtta geçmiş bir incident'a ya da runbook'a atıf yaptıysa, bu bilginin nereden geldiğini buradan görürsünüz.",
    terms: [
      ["Working memory", "Tek bir konuşma boyunca tutulan odak: hangi servis, hangi zaman aralığı."],
      ["Short-term memory", "Aynı konuşmanın önceki mesajları."],
      ["Long-term memory", "Konuşmalar arasında kalıcı kayıtlar: benzer geçmiş analizler, onaylı runbook'lar."],
      ["Checkpoint", "Yarıda kalan bir turu sürdürmek için saklanan ara durum; uzun vadeli hafıza değildir."],
    ],
    links: [{ label: "Bilgi bankasını aç", to: "/knowledge" }],
  },
  middleware: {
    what: "Middleware, agent'ın her LLM ve MCP çağrısının önünde ve arkasında çalışan denetim adımlarıdır: bütçe sınırı, hassas veri maskeleme, tekrar deneme gibi.",
    why: "Bir çağrı engellendiyse, yavaşladıysa ya da içeriği değiştiyse hangi adımın bunu yaptığını burada görürsünüz.",
    terms: [
      ["Zorunlu middleware", "Kapatılamaz; güvenlik ve kayıt için her çağrıda çalışır."],
      ["Overhead", "Middleware'in çağrıya eklediği süre."],
      ["Yürütme sırası", "Adımların bir çağrıda gerçekte hangi sırayla çalıştığı."],
    ],
    links: [{ label: "Güvenlik kararlarını aç (Guardrails)", to: "/guardrails" }],
  },
  guardrails: {
    what: "Guardrail, agent'ın girdisini, tool çağrılarını ve yanıtını denetleyen güvenlik kuralıdır. Kararı sabit kurallar verir; dil modeli güvenlik kararı vermez.",
    why: "Bir soru reddedildiyse, bir yanıt düzeltildiyse ya da bir işlem onay bekliyorsa nedenini burada bulursunuz.",
    terms: [
      ["Policy", "Bir kuralın açık / kapalı durumu ve sınır değerleri."],
      ["Sanitize", "İçerik engellenmez; riskli kısım maskelenir ya da çıkarılır."],
      ["Fail-closed", "Kural değerlendirilemezse işlem durdurulur."],
    ],
    links: [{ label: "Bekleyen onayları aç", to: "/approvals" }],
  },
  trace: {
    what: "Agent Trace, bir çalıştırmanın adım adım kaydıdır: agent hangi adımı (node) çalıştırdı, o adıma ne girdi, ne çıktı, hangi LLM çağrısı yapıldı.",
    why: "“Agent bu sonuca nasıl vardı?” sorusunun en ayrıntılı yanıtı buradadır. Önce Agent Activity'deki adım özetine, ayrıntı gerekince buraya bakın.",
    terms: [
      ["Node", "Workflow'un tek bir adımı; örneğin veri toplama ya da açıklama yazma."],
      ["State", "Adımlar arasında taşınan veri. Her adım state'in bir bölümünü değiştirir."],
      ["Run", "Bir sorunun ya da zamanlanmış kontrolün baştan sona tek çalıştırması."],
    ],
    links: [{ label: "Adım özetini aç (Agent Activity)", to: "/activity" }],
  },
  mcp: {
    what: "MCP çağrısı, agent'ın Dynatrace'ten veri okumak için gönderdiği tek bir istektir. Rapordaki her ölçülen bulgu bu çağrılardan birine dayanır.",
    why: "Bir bulgunun hangi veriden çıktığını ya da bir verinin neden eksik olduğunu (hata, timeout, yetki) buradan doğrularsınız.",
    terms: [
      ["Connector", "Platformun bir MCP sunucusuna bağlantı tanımı: adres, yetki, süre sınırı."],
      ["Evidence", "Bir çağrının döndürdüğü veriden çıkarılan, kimliği olan bulgu."],
      ["Sonuç durumu", "OK: veri geldi. EMPTY: yanıt geldi, kayıt yok. TIMEOUT / ERROR: yanıt alınamadı; bu veri 0 sayılmaz."],
    ],
    links: [{ label: "Investigation geçmişini aç", to: "/investigations" }],
  },
};

export function ConceptHelp({ k }: { k: ConceptKey }) {
  const c = CONCEPTS[k];
  return (
    <details className="w-full max-w-3xl">
      <summary className="cursor-pointer text-xs text-accent-text hover:underline">Bu sayfa neyi gösterir?</summary>
      <div className="mt-1.5 space-y-1.5 rounded-md border border-line bg-surface-2 p-3 text-xs text-ink-2">
        <p>{c.what}</p>
        <p>
          <span className="font-medium text-ink">Ne zaman bakılır? </span>
          {c.why}
        </p>
        <dl className="space-y-0.5">
          {c.terms.map(([t, d]) => (
            <div key={t} className="flex flex-wrap gap-x-1.5">
              <dt className="font-medium text-ink">{t}:</dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
        <p className="flex flex-wrap gap-x-3">
          {c.links.map((l) => (
            <Link key={l.to} to={l.to} className="text-accent-text hover:underline">
              {l.label}
            </Link>
          ))}
        </p>
      </div>
    </details>
  );
}
