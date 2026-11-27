import { Activity } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { Button, Input, Label } from "@/components/ui/primitives";
import { useAuth } from "@/services/auth";
import type { ApiError } from "@/services/api";

export default function LoginPage() {
  const { user, login } = useAuth();
  const loc = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={(loc.state as { from?: string } | null)?.from ?? "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await login(username.trim(), password);
    } catch (x) {
      setErr((x as ApiError).message);
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <div className="grid min-h-full place-items-center bg-page p-6">
      <form onSubmit={submit} className="w-full max-w-xs rounded-lg border border-line bg-surface p-5" aria-label="giriş formu">
        <div className="mb-5 flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-md bg-ink text-page">
            <Activity className="size-4" />
          </span>
          <div className="leading-tight">
            <h1 className="text-[13px] font-semibold text-ink">airlab SRE</h1>
            <p className="text-xs text-muted">Agent Management Platform</p>
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <Label htmlFor="u">Kullanıcı adı</Label>
            <Input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
          </div>
          <div>
            <Label htmlFor="p">Parola</Label>
            <Input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {err && (
            <p role="alert" className="rounded-md border border-l-2 border-st-critical/50 bg-surface px-2.5 py-1.5 text-xs text-st-critical-ink">
              {err}
            </p>
          )}
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            Giriş yap
          </Button>
        </div>
      </form>
    </div>
  );
}
