import { Loader2 } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider, useLocation } from "react-router";
import { EmptyState } from "@/components/ui/states";
import { AppLayout } from "@/layouts/AppLayout";
import LoginPage from "@/pages/LoginPage";
import { useAuth } from "@/services/auth";

const Dashboard = lazy(() => import("@/pages/DashboardPage"));
const Investigate = lazy(() => import("@/pages/InvestigatePage"));
const Activity = lazy(() => import("@/pages/ActivityPage"));
const History = lazy(() => import("@/pages/HistoryPage"));
const InvestigationDetail = lazy(() => import("@/pages/InvestigationDetailPage"));
const Incidents = lazy(() => import("@/pages/IncidentsPage"));
const IncidentDetail = lazy(() => import("@/pages/IncidentDetailPage"));
const Rcas = lazy(() => import("@/pages/RcaListPage"));
const RcaDetail = lazy(() => import("@/pages/RcaDetailPage"));
const Knowledge = lazy(() => import("@/pages/KnowledgePage"));
const Approvals = lazy(() => import("@/pages/ApprovalsPage"));
const LlmObservability = lazy(() => import("@/pages/LlmObservabilityPage"));
const Settings = lazy(() => import("@/pages/SettingsPage"));

function Loading() {
  return (
    <div className="grid h-full place-items-center text-muted">
      <Loader2 className="size-6 animate-spin" aria-label="yükleniyor" />
    </div>
  );
}

function Protected({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <>{children}</>;
}

const s = (el: ReactNode) => <Suspense fallback={<Loading />}>{el}</Suspense>;

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <Protected>
        <AppLayout />
      </Protected>
    ),
    children: [
      { index: true, element: s(<Dashboard />) },
      { path: "investigate/:conversationId?", element: s(<Investigate />) },
      { path: "activity/:investigationId?", element: s(<Activity />) },
      { path: "investigations", element: s(<History />) },
      { path: "investigations/:id", element: s(<InvestigationDetail />) },
      { path: "incidents", element: s(<Incidents />) },
      { path: "incidents/:id", element: s(<IncidentDetail />) },
      { path: "rca", element: s(<Rcas />) },
      { path: "rca/:id", element: s(<RcaDetail />) },
      { path: "knowledge", element: s(<Knowledge />) },
      { path: "approvals", element: s(<Approvals />) },
      { path: "llm", element: s(<LlmObservability />) },
      { path: "settings/:section?", element: s(<Settings />) },
      { path: "*", element: <EmptyState title="Sayfa bulunamadı" description="Adres hatalı olabilir." /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
