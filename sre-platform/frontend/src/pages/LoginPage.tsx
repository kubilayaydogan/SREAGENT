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
    <div className="grid min-h-full place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-6" aria-label="giriş formu">
        <div className="mb-6 flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-md bg-accent text-white">
            <Activity className="size-5" />
          </div>
          <div>
            <h1 className="text-base font-semibold text-ink">airlab SRE Operations</h1>
            <p className="text-xs text-muted">AI destekli SRE platformu</p>
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
            <p role="alert" className="rounded-md border border-st-critical/40 bg-st-critical/10 px-3 py-2 text-xs text-[#f08a8a]">
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
