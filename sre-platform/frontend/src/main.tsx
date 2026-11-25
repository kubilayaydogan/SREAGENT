import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import App from "./App";
import { AuthProvider } from "./services/auth";
import { ApiError } from "./services/api";
import { applyInitialTheme, ThemeProvider, useTheme } from "./lib/theme";
import "./index.css";

applyInitialTheme();

const qc = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // 4xx tekrar denenmez (yetki / bulunamadı / doğrulama); ağ ve 5xx en fazla 2 kez
      retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
    },
  },
});

function ThemedToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="bottom-right" closeButton toastOptions={{ className: "!rounded-md !text-[13px]" }} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <App />
          <ThemedToaster />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
