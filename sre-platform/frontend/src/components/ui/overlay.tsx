import * as D from "@radix-ui/react-dialog";
import * as T from "@radix-ui/react-tabs";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[1px]" />
        <D.Content
          className={cn(
            "fixed top-1/2 left-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-line-strong bg-surface shadow-2xl",
            wide ? "max-w-3xl" : "max-w-lg",
          )}
        >
          <div className="flex items-start justify-between border-b border-line px-5 py-3">
            <div>
              <D.Title className="text-sm font-semibold text-ink">{title}</D.Title>
              {description ? (
                <D.Description className="mt-0.5 text-xs text-muted">{description}</D.Description>
              ) : (
                <D.Description className="sr-only">{title}</D.Description>
              )}
            </div>
            <D.Close className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink" aria-label="kapat">
              <X className="size-4" />
            </D.Close>
          </div>
          <div className="px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export function Tabs({ value, onValueChange, tabs, children, className }: {
  value: string;
  onValueChange: (v: string) => void;
  tabs: { value: string; label: ReactNode }[];
  children: ReactNode;
  className?: string;
}) {
  return (
    <T.Root value={value} onValueChange={onValueChange} className={className}>
      <T.List className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.map((t) => (
          <T.Trigger
            key={t.value}
            value={t.value}
            className="-mb-px border-b-2 border-transparent px-3 py-2 text-sm whitespace-nowrap text-muted hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink"
          >
            {t.label}
          </T.Trigger>
        ))}
      </T.List>
      {children}
    </T.Root>
  );
}

export const TabPanel = T.Content;
