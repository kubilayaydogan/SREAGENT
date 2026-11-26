/** Bir çalıştırmada geçerli olan model ve seçim kaynağı metinleri (backend: settings/runtime.py effective_models).
 *  Öncelik (gömülü SRE Agent): üst bar seçimi > görev LLM profili > ortam değişkeni. Özel agent'lar kendi model
 *  ayarını kullanır (node > agent > profil). */
import type { EffectiveModel, LlmRunInfo } from "@/types/api";

export const MODEL_SOURCE_LABEL: Record<string, string> = {
  override: "üst bardaki model seçimi",
  profile: "LLM profili",
  env: "ortam değişkeni (deployment)",
  node: "node ayarı",
  agent: "agent varsayılanı",
  platform: "platform ayarı",
  none: "LLM yok",
};

export const LLM_REASON_LABEL: Record<string, string> = {
  llm_not_configured: "LLM yapılandırılmamış (API anahtarı yok)",
  llm_disabled: "LLM, Settings › Agent'ta kapalı",
  profile_disabled: "seçili LLM profili yok ya da devre dışı",
  profile_model_denied: "profilin modeli izinli model listesinde değil",
  profile_budget_exhausted: "LLM profilinin aylık bütçesi doldu",
  profile_secret_error: "LLM profilinin API anahtarı çözülemedi",
  profile_egress_denied: "LLM profilinin adresi ağ kuralına takıldı",
  profile_unavailable: "LLM profili kullanılamıyor",
  run_choice: "bu çalıştırmada “Claude ile açıklama üret” seçilmedi",
  scheduled_llm_off: "zamanlanmış koşularda LLM kapalı (Settings › Agent)",
  incident_llm_off: "incident koşularında LLM kapalı (Settings › Agent)",
  not_requested: "bu çalıştırma LLM istemedi",
  unavailable: "LLM bu çalıştırmada kullanılamadı",
};

export function sourceLabel(e: Pick<EffectiveModel, "source" | "profile"> | null | undefined): string {
  if (!e?.source) return "kaydedilmedi";
  const base = MODEL_SOURCE_LABEL[e.source] ?? e.source;
  return e.source === "profile" && e.profile ? `${base} “${e.profile}”` : base;
}

export const reasonLabel = (r: string | null | undefined) => (r ? (LLM_REASON_LABEL[r] ?? r) : "neden kaydedilmedi");

/** Analiz açıklaması için tek satır: model + kaynak ya da LLM'in neden kullanılmadığı. */
export function llmRunText(run: LlmRunInfo | null | undefined, rca: EffectiveModel | null | undefined): { model: string | null; detail: string } {
  if (run && run.stage === "explain" && run.used === false) return { model: null, detail: reasonLabel(run.reason) };
  const e = rca ?? null;
  if (!e) return { model: null, detail: "bu çalıştırmada model seçimi kaydedilmedi" };
  if (!e.model) return { model: null, detail: reasonLabel(e.reason) };
  return { model: e.model, detail: sourceLabel(e) };
}
