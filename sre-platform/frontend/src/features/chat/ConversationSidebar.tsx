import { Archive, MessageSquarePlus, Pencil } from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";
import { SearchInput } from "@/components/filters";
import { Button, Input, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useConversationMutations, useConversations } from "@/hooks/queries";
import { cn, fmtRelative } from "@/lib/utils";

export function ConversationSidebar({ onNew, activeId }: { onNew: () => void; activeId?: string }) {
  const [q, setQ] = useState("");
  const { data, isLoading, error, refetch } = useConversations(q || undefined);
  const { rename } = useConversationMutations();
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-2 border-b border-line p-3">
        <Button variant="primary" className="w-full" onClick={onNew}>
          <MessageSquarePlus /> Yeni konuşma
        </Button>
        <SearchInput value={q} onChange={setQ} placeholder="Konuşmalarda ara" className="w-full" />
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {isLoading && (
          <div className="space-y-2 p-1">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        )}
        {error && <ErrorState error={error} onRetry={() => refetch()} />}
        {data && !data.items.length && <EmptyState title={q ? "Eşleşen konuşma yok" : "Henüz konuşma yok"} />}
        <ul className="space-y-0.5">
          {data?.items.map((c) => (
            <li key={c.id} className="group relative">
              {editing === c.id ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (title.trim()) rename.mutate({ id: c.id, title: title.trim() });
                    setEditing(null);
                  }}
                >
                  <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={() => setEditing(null)} maxLength={200} className="h-8 text-xs" />
                </form>
              ) : (
                <NavLink
                  to={`/investigate/${c.id}`}
                  className={({ isActive }) =>
                    cn("block rounded-md px-2.5 py-2 pr-14 text-sm", isActive || activeId === c.id ? "bg-surface-3 text-ink" : "text-ink-2 hover:bg-surface-2")
                  }
                >
                  <div className="truncate">{c.title}</div>
                  <div className="text-[11px] text-muted">{fmtRelative(c.updated_at)}</div>
                </NavLink>
              )}
              {editing !== c.id && (
                <div className="absolute top-1.5 right-1 hidden gap-0.5 group-focus-within:flex group-hover:flex">
                  <button
                    className="rounded p-1 text-muted hover:bg-line-strong hover:text-ink"
                    aria-label="yeniden adlandır"
                    onClick={() => {
                      setTitle(c.title);
                      setEditing(c.id);
                    }}
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button className="rounded p-1 text-muted hover:bg-line-strong hover:text-ink" aria-label="arşivle" onClick={() => rename.mutate({ id: c.id, archived: true })}>
                    <Archive className="size-3.5" />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
