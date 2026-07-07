import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/AuthProvider";
import { toast } from "sonner";
import { Shield, Lock, Server, KeyRound } from "lucide-react";

function AuthShell({ children }) {
    return (
        <div className="min-h-screen grid grid-cols-1 lg:grid-cols-5">
            <aside className="hidden lg:flex lg:col-span-2 grid-backdrop relative border-r border-[hsl(var(--border))]">
                <div className="absolute inset-0 bg-gradient-to-b from-transparent via-[hsl(var(--background))/0] to-[hsl(var(--background))]" />
                <div className="relative flex flex-col justify-between p-10 z-10 w-full">
                    <Link to="/" className="flex items-center gap-2" data-testid="auth-brand-link">
                        <div className="w-7 h-7 border border-[hsl(var(--primary))] flex items-center justify-center">
                            <div className="w-2 h-2 bg-[hsl(var(--primary))]" />
                        </div>
                        <span className="font-heading font-bold text-white text-lg tracking-tight">
                            EvidentAI
                        </span>
                    </Link>
                    <div className="space-y-5 max-w-md">
                        <div className="label-caps">Phase 01 · Foundations</div>
                        <h1 className="font-heading text-3xl sm:text-4xl font-bold text-white leading-tight">
                            Multi-tenant analytics <br /> built on{" "}
                            <span className="text-[hsl(var(--primary))]">isolation</span>, not trust.
                        </h1>
                        <p className="text-sm text-[hsl(var(--muted-foreground))] leading-relaxed">
                            Row-level security at the Postgres layer, envelope-encrypted per-tenant
                            file storage, sandboxed deterministic profiling. Agents come later — the
                            foundation is uncompromising from day one.
                        </p>
                        <ul className="text-xs font-mono text-[hsl(var(--muted-foreground))] space-y-1.5 pt-4">
                            <li className="flex items-center gap-2">
                                <Shield size={12} className="text-[hsl(var(--success))]" />
                                PostgreSQL RLS · FORCE ROW LEVEL SECURITY
                            </li>
                            <li className="flex items-center gap-2">
                                <Lock size={12} className="text-[hsl(var(--success))]" />
                                AES-256-GCM · per-tenant DEK · envelope wrap
                            </li>
                            <li className="flex items-center gap-2">
                                <Server size={12} className="text-[hsl(var(--success))]" />
                                ClamAV scan · Excel macro/OLE reject · CSV formula defense
                            </li>
                            <li className="flex items-center gap-2">
                                <KeyRound size={12} className="text-[hsl(var(--success))]" />
                                RS256 JWT · rotating refresh · reuse detection
                            </li>
                        </ul>
                    </div>
                    <div className="text-[10px] font-mono text-[hsl(var(--muted-foreground))]">
                        © EvidentAI · confidential
                    </div>
                </div>
            </aside>
            <section className="lg:col-span-3 flex items-center justify-center px-6 py-12">
                <div className="w-full max-w-md">{children}</div>
            </section>
        </div>
    );
}

export function LoginPage() {
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const { login } = useAuth();
    const nav = useNavigate();

    const submit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await login(email, password);
            toast.success("Signed in");
            nav("/datasets");
        } catch (err) {
            toast.error(err?.response?.data?.detail || "Sign in failed");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <AuthShell>
            <div className="space-y-8">
                <div>
                    <div className="label-caps mb-2">Sign in</div>
                    <h2 className="font-heading text-2xl font-bold text-white">
                        Access your workspace
                    </h2>
                </div>
                <form onSubmit={submit} className="space-y-4" data-testid="login-form">
                    <div>
                        <label className="label-caps block mb-1.5">Email</label>
                        <input
                            data-testid="login-email-input"
                            required
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            className="w-full bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))] focus:border-[hsl(var(--primary))] rounded-sm"
                            placeholder="you@company.com"
                            autoComplete="email"
                        />
                    </div>
                    <div>
                        <label className="label-caps block mb-1.5">Password</label>
                        <input
                            data-testid="login-password-input"
                            required
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            className="w-full bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))] focus:border-[hsl(var(--primary))] rounded-sm"
                            placeholder="••••••••••"
                            autoComplete="current-password"
                        />
                    </div>
                    <button
                        data-testid="login-submit-button"
                        type="submit"
                        disabled={submitting}
                        className="w-full bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] disabled:opacity-60 text-white font-medium px-3 py-2 text-sm transition-colors rounded-sm"
                    >
                        {submitting ? "Signing in…" : "Sign in"}
                    </button>
                </form>
                <div className="text-xs text-[hsl(var(--muted-foreground))]">
                    New here?{" "}
                    <Link
                        to="/register"
                        data-testid="link-to-register"
                        className="text-[hsl(var(--primary))] hover:underline"
                    >
                        Create a tenant &rarr;
                    </Link>
                </div>
            </div>
        </AuthShell>
    );
}

export function RegisterPage() {
    const [tenantName, setTenantName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const { register } = useAuth();
    const nav = useNavigate();

    const submit = async (e) => {
        e.preventDefault();
        if (password.length < 10) {
            toast.error("Password must be at least 10 characters");
            return;
        }
        setSubmitting(true);
        try {
            await register(tenantName, email, password);
            toast.success("Workspace created");
            nav("/datasets");
        } catch (err) {
            const d = err?.response?.data?.detail;
            const msg = Array.isArray(d) ? d[0]?.msg : d;
            toast.error(msg || "Registration failed");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <AuthShell>
            <div className="space-y-8">
                <div>
                    <div className="label-caps mb-2">Create workspace</div>
                    <h2 className="font-heading text-2xl font-bold text-white">
                        Start a new tenant
                    </h2>
                    <p className="text-xs text-[hsl(var(--muted-foreground))] mt-2 font-mono">
                        This provisions an isolated Postgres RLS partition and a per-tenant
                        encryption key.
                    </p>
                </div>
                <form onSubmit={submit} className="space-y-4" data-testid="register-form">
                    <div>
                        <label className="label-caps block mb-1.5">Tenant name</label>
                        <input
                            data-testid="register-tenant-input"
                            required
                            minLength={2}
                            value={tenantName}
                            onChange={(e) => setTenantName(e.target.value)}
                            className="w-full bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))] focus:border-[hsl(var(--primary))] rounded-sm"
                            placeholder="Acme Analytics"
                        />
                    </div>
                    <div>
                        <label className="label-caps block mb-1.5">Admin email</label>
                        <input
                            data-testid="register-email-input"
                            required
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            className="w-full bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))] focus:border-[hsl(var(--primary))] rounded-sm"
                            placeholder="admin@company.com"
                            autoComplete="email"
                        />
                    </div>
                    <div>
                        <label className="label-caps block mb-1.5">Password (min 10)</label>
                        <input
                            data-testid="register-password-input"
                            required
                            minLength={10}
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            className="w-full bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))] focus:border-[hsl(var(--primary))] rounded-sm"
                            placeholder="A strong passphrase"
                            autoComplete="new-password"
                        />
                    </div>
                    <button
                        data-testid="register-submit-button"
                        type="submit"
                        disabled={submitting}
                        className="w-full bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] disabled:opacity-60 text-white font-medium px-3 py-2 text-sm transition-colors rounded-sm"
                    >
                        {submitting ? "Provisioning…" : "Create workspace"}
                    </button>
                </form>
                <div className="text-xs text-[hsl(var(--muted-foreground))]">
                    Already have an account?{" "}
                    <Link
                        to="/login"
                        data-testid="link-to-login"
                        className="text-[hsl(var(--primary))] hover:underline"
                    >
                        Sign in &rarr;
                    </Link>
                </div>
            </div>
        </AuthShell>
    );
}
