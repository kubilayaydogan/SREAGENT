import { SendHorizontal, Square } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { AppSelect } from "@/components/filters";
import { Button, Input, Select } from "@/components/ui/primitives";

export interface TimeChoice {
  minutes?: number;
  start?: string;
  end?: string;
}

const PRESETS = [
  { v: "", l: "Zaman: sorudan" },
  { v: "15", l: "Son 15 dk" },
  { v: "30", l: "Son 30 dk" },
  { v: "60", l: "Son 1 saat" },
  { v: "180", l: "Son 3 saat" },
  { v: "360", l: "Son 6 saat" },
  { v: "1440", l: "Son 24 saat" },
  { v: "custom", l: "Özel aralık…" },
];

/** Yerel datetime-local değerini UTC ISO'ya çevirir. */
function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

export function Composer({ onSend, onCancel, busy, disabled, disabledReason, app, onApp, agentSlot, footnote }: {
  onSend: (text: string, time: TimeChoice | null) => void;
  onCancel: () => void;
  busy: boolean;
  disabled?: boolean;
  disabledReason?: string;
  app: string;
  onApp: (v: string) => void;
  /** Gönder düğmesinin yanında agent seçici (Agent Builder). */
  agentSlot?: ReactNode;
  footnote?: ReactNode;
}) {
  const [text, setText] = useState("");
  const [preset, setPreset] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const customInvalid = preset === "custom" && (!from || !to || new Date(to) <= new Date(from));

  function send() {
    const t = text.trim();
    if (!t || busy || disabled || customInvalid) return;
    const time: TimeChoice | null =
      preset === "custom" ? { start: toIso(from), end: toIso(to) } : preset ? { minutes: Number(preset) } : null;
    onSend(t, time);
    setText("");
    ref.current?.focus();
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="border-t border-line bg-surface p-3">
      <div className="mx-auto max-w-4xl">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <AppSelect value={app} onChange={onApp} allLabel="Servis: tümü" className="h-7 w-40 text-xs" />
          <Select aria-label="zaman aralığı" value={preset} onChange={(e) => setPreset(e.target.value)} className="h-7 w-36 text-xs">
            {PRESETS.map((p) => (
              <option key={p.v} value={p.v}>
                {p.l}
              </option>
            ))}
          </Select>
          {preset === "custom" && (
            <>
              <Input aria-label="başlangıç" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} className="h-7 w-48 text-xs" />
              <span className="text-xs text-muted">→</span>
              <Input aria-label="bitiş" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} className="h-7 w-48 text-xs" />
              {customInvalid && <span className="text-xs text-st-warning-ink">geçerli bir aralık seçin</span>}
            </>
          )}
        </div>
        <div className="flex items-end gap-2 rounded-lg border border-line-strong bg-surface p-1.5 focus-within:border-accent">
          <textarea
            ref={ref}
            aria-label="soru"
            rows={Math.min(6, Math.max(1, text.split("\n").length))}
            maxLength={4000}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            disabled={disabled}
            placeholder={disabled ? disabledReason : "Örn. Reservation uygulaması neden yavaşladı?  (Enter: gönder, Shift+Enter: satır)"}
            className="max-h-40 flex-1 resize-none bg-transparent px-1.5 py-1 text-[13px] text-ink placeholder:text-muted focus:outline-none"
          />
          {agentSlot}
          {busy ? (
            <Button variant="outline" size="md" onClick={onCancel} aria-label="durdur">
              <Square /> Durdur
            </Button>
          ) : (
            <Button variant="primary" size="md" onClick={send} disabled={!text.trim() || disabled || customInvalid} aria-label="gönder">
              <SendHorizontal />
            </Button>
          )}
        </div>
        {footnote}
        <p className="mt-1.5 text-2xs text-muted">
          Agent yalnız okuma yapar (Dynatrace MCP query'leri). Sonuçlar correlation temelli hipotezdir; kritik aksiyonlar onay akışından geçer.{" "}
          Sorunuz ve oluşan analiz (investigation) ekipteki tüm kullanıcılara görünür; sohbet mesajları yalnız size aittir.
        </p>
      </div>
    </div>
  );
}
