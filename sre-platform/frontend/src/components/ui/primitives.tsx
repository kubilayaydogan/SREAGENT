/* Temel bileşenler (Tailwind + cva). Ölçüler: kontrol yüksekliği 28/32 px, radius 3–6 px, gölge yok.
   Yüzey hiyerarşisi kenarlıkla kurulur; her içerik ayrı karta konmaz — Panel bir bölümdür, iç içe kart değildir. */
import { cva, type VariantProps } from "class-variance-authority";
import { ChevronRight, Loader2 } from "lucide-react";
import {
  forwardRef,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors duration-100 disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-ink hover:bg-accent/90",
        secondary: "border border-line-strong bg-surface text-ink hover:bg-surface-2",
        outline: "border border-line-strong bg-transparent text-ink-2 hover:bg-surface-2 hover:text-ink",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        danger: "bg-st-critical text-white hover:bg-st-critical/90",
      },
      size: {
        sm: "h-7 px-2 text-xs [&_svg]:size-3.5",
        md: "h-8 px-3 text-[13px] [&_svg]:size-4",
        lg: "h-9 px-4 text-[13px] [&_svg]:size-4",
        icon: "size-7 [&_svg]:size-4",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, loading, children, disabled, ...p }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...p}>
    {loading && <Loader2 className="animate-spin" />}
    {children}
  </button>
));
Button.displayName = "Button";

/** Kenarlıklı yüzey. Sayfa düzeninde üst seviye blok; iç içe kullanılmaz. */
export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-surface", className)} {...p} />;
}

export function CardHeader({ title, description, actions, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-10 items-center justify-between gap-3 border-b border-line px-3 py-2", className)}>
      <div className="min-w-0">
        <h2 className="truncate text-[13px] font-semibold text-ink">{title}</h2>
        {description && <p className="truncate text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-3", className)} {...p} />;
}

/** Kart içinde ya da sayfa akışında bölüm başlığı — yeni bir kart açmadan gruplar. */
export function SectionTitle({ children, actions, className, id }: { children: ReactNode; actions?: ReactNode; className?: string; id?: string }) {
  return (
    <div className={cn("mb-2 flex items-center justify-between gap-2", className)}>
      <h3 id={id} className="text-xs font-semibold text-ink-2">
        {children}
      </h3>
      {actions}
    </div>
  );
}

/** Progressive disclosure: başlık her zaman görünür, ayrıntı istenince açılır. */
export function Disclosure({ title, meta, children, defaultOpen = false, className }: {
  title: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 py-1.5 text-left text-[13px] text-ink hover:text-ink"
      >
        <ChevronRight className={cn("size-3.5 shrink-0 text-muted transition-transform duration-100", open && "rotate-90")} />
        <span className="min-w-0 flex-1">{title}</span>
        {meta && <span className="shrink-0 text-xs text-muted">{meta}</span>}
      </button>
      {open && (
        <div id={id} className="pb-2 pl-5">
          {children}
        </div>
      )}
    </div>
  );
}

const fieldCls =
  "w-full rounded-md border border-line-strong bg-surface px-2.5 text-[13px] text-ink placeholder:text-muted focus:border-accent focus:outline-none disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(fieldCls, "h-8", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(fieldCls, "py-1.5", className)} {...p} />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...p }, ref) => (
  <select ref={ref} className={cn(fieldCls, "h-8 pr-7", className)} {...p}>
    {children}
  </select>
));
Select.displayName = "Select";

export function Label({ children, htmlFor, className }: { children: ReactNode; htmlFor?: string; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("mb-1 block text-xs font-medium text-ink-2", className)}>
      {children}
    </label>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-sm bg-surface-3/70", className)} />;
}

/** Nötr etiket (sayaç, tür). Durum için badges.tsx'teki durum bileşenlerini kullan. */
export function Badge({ className, children, title }: { className?: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 rounded-sm border border-line-strong px-1.5 py-px text-xs text-ink-2", className)}>
      {children}
    </span>
  );
}

/** Segmentli seçim (zaman aralığı, metric). Tek satır, radiogroup semantiği. */
export function Segmented<T extends string>({ value, onChange, options, label, size = "sm", className }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; title?: string }[];
  label: string;
  size?: "sm" | "xs";
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex rounded-md border border-line-strong bg-surface p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          title={o.title}
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-sm font-medium whitespace-nowrap transition-colors duration-100",
            size === "sm" ? "h-6 px-2 text-xs" : "h-5 px-1.5 text-2xs",
            value === o.value ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-line-strong bg-surface-2 px-1 font-mono text-[10px] text-muted">{children}</kbd>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-xs text-ink-2", className)}>{children}</span>;
}

/** Tanım listesi satırı: anahtar solda (muted), değer sağda. Yan panellerde meta bilgi için. */
export function Field({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 py-1", className)}>
      <dt className="shrink-0 text-xs text-muted">{label}</dt>
      <dd className="min-w-0 truncate text-right text-[13px] text-ink">{children}</dd>
    </div>
  );
}
