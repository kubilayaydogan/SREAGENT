import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Ajan / RCA markdown'ı. Ham HTML KAPALI (react-markdown varsayılanı) → XSS yok; bağlantılar yeni sekmede, noreferrer. */
export function Markdown({ children, className, streaming }: { children: string; className?: string; streaming?: boolean }) {
  return (
    <div className={cn("prose-sre", streaming && "caret", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {c}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
