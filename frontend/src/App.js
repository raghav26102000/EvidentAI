import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/AuthProvider";
import { Toaster } from "sonner";
import AppLayout from "./components/AppLayout";
import { LoginPage, RegisterPage } from "./pages/Auth";
import DatasetsList from "./pages/DatasetsList";
import DatasetUpload from "./pages/DatasetUpload";
import DatasetDetail from "./pages/DatasetDetail";
import AnalysisDashboard from "./pages/AnalysisDashboard";
import AgentTrail from "./pages/AgentTrail";
import AuditLog from "./pages/AuditLog";
import Settings from "./pages/Settings";
import "./App.css";

function Protected({ children }) {
    const { user, loading } = useAuth();
    if (loading)
        return (
            <div className="min-h-screen flex items-center justify-center font-mono text-sm text-[hsl(var(--muted-foreground))]">
                initialising…
            </div>
        );
    if (!user) return <Navigate to="/login" replace />;
    return children;
}

function Public({ children }) {
    const { user, loading } = useAuth();
    if (loading)
        return (
            <div className="min-h-screen flex items-center justify-center font-mono text-sm text-[hsl(var(--muted-foreground))]">
                initialising…
            </div>
        );
    if (user) return <Navigate to="/datasets" replace />;
    return children;
}

function App() {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Toaster
                    theme="dark"
                    position="bottom-right"
                    toastOptions={{
                        style: {
                            background: "hsl(240 10% 3.9%)",
                            border: "1px solid hsl(240 4% 16%)",
                            fontFamily: "JetBrains Mono, monospace",
                            fontSize: 12,
                            borderRadius: 2,
                            color: "hsl(0 0% 98%)",
                        },
                    }}
                />
                <Routes>
                    <Route path="/login" element={<Public><LoginPage /></Public>} />
                    <Route path="/register" element={<Public><RegisterPage /></Public>} />
                    <Route
                        path="/"
                        element={
                            <Protected>
                                <AppLayout />
                            </Protected>
                        }
                    >
                        <Route index element={<Navigate to="/datasets" replace />} />
                        <Route path="datasets" element={<DatasetsList />} />
                        <Route path="datasets/:id" element={<DatasetDetail />} />
                        <Route path="datasets/:id/analysis" element={<AnalysisDashboard />} />
                        <Route path="agent-trail/:jobId" element={<AgentTrail />} />
                        <Route path="upload" element={<DatasetUpload />} />
                        <Route path="audit" element={<AuditLog />} />
                        <Route path="settings" element={<Settings />} />
                    </Route>
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </BrowserRouter>
        </AuthProvider>
    );
}

export default App;
