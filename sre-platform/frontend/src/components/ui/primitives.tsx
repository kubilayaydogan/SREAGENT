/* shadcn/ui tarzı temel bileşenler (Tailwind + cva). Tek dosyada küçük, yeniden kullanılabilir parçalar. */
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-accent text-accent-ink hover:bg-accent/90",
        secondary: "bg-surface-3 text-ink hover:bg-line-strong",
        outline: "border border-line-strong bg-transparent text-ink-2 hover:bg-surface-2 hover:text-ink",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        danger: "bg-st-critical text-white hover:bg-st-critical/90",
      },
      size: { sm: "h-7 px-2.5 text-xs", md: "h-9 px-3.5", lg: "h-10 px-5", icon: "size-8" },
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

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-line bg-surface", className)} {...p} />;
}

export function CardHeader({ title, description, actions, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <h3 className="truncate text-sm font-semibold text-ink">{title}</h3>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...p} />;
}

const fieldCls =
  "w-full rounded-md border border-line-strong bg-surface-2 px-3 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none disabled:opacity-60";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(fieldCls, "h-9", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(fieldCls, "py-2", className)} {...p} />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, children, ...p }, ref) => (
  <select ref={ref} className={cn(fieldCls, "h-9 pr-8", className)} {...p}>
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
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-surface-3/70", className)} />;
}

export function Badge({ className, children, title }: { className?: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 rounded-full border border-line-strong px-2 py-0.5 text-xs font-medium text-ink-2", className)}>
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border border-line-strong bg-surface-2 px-1 font-mono text-[10px] text-muted">{children}</kbd>;
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-xs text-ink-2", className)}>{children}</span>;
}
