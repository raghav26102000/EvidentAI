import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/AuthProvider";
import { LogOut, Upload, Database, Shield, Settings as SettingsIcon, Activity } from "lucide-react";

function NavItem({ to, icon: Icon, children, testId }) {
    return (
        <NavLink
            to={to}
            data-testid={testId}
            className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-1.5 text-sm rounded-sm transition-colors ${
                    isActive
                        ? "text-white bg-[hsl(var(--secondary))] border border-[hsl(var(--border))]"
                        : "text-[hsl(var(--muted-foreground))] hover:text-white hover:bg-[hsl(var(--secondary))]"
                }`
            }
        >
            <Icon size={14} strokeWidth={1.6} />
            <span>{children}</span>
        </NavLink>
    );
}

export default function AppLayout() {
    const { user, tenant, logout } = useAuth();
    const nav = useNavigate();
    if (!user || !tenant) return null;

    return (
        <div className="min-h-screen">
            <header className="border-b border-[hsl(var(--border))] bg-[hsl(var(--background))] sticky top-0 z-30">
                <div className="max-w-[1400px] mx-auto px-6 py-3 flex items-center gap-6">
                    <Link to="/datasets" className="flex items-center gap-2" data-testid="brand-link">
                        <div className="w-6 h-6 border border-[hsl(var(--primary))] flex items-center justify-center">
                            <div className="w-1.5 h-1.5 bg-[hsl(var(--primary))]" />
                        </div>
                        <span className="font-heading font-bold tracking-tight text-white">
                            EvidentAI
                        </span>
                        <span className="text-[hsl(var(--muted-foreground))] text-xs font-mono ml-1">
                            / {tenant.slug}
                        </span>
                    </Link>

                    <nav className="flex items-center gap-1 ml-4">
                        <NavItem to="/datasets" icon={Database} testId="nav-datasets">
                            Datasets
                        </NavItem>
                        <NavItem to="/upload" icon={Upload} testId="nav-upload">
                            Upload
                        </NavItem>
                        <NavItem to="/audit" icon={Activity} testId="nav-audit">
                            Audit
                        </NavItem>
                        <NavItem to="/settings" icon={SettingsIcon} testId="nav-settings">
                            Settings
                        </NavItem>
                    </nav>

                    <div className="ml-auto flex items-center gap-3">
                        <div className="hidden sm:flex items-center gap-2 text-xs">
                            <Shield size={12} className="text-[hsl(var(--success))]" />
                            <span className="label-caps">RLS ENFORCED</span>
                        </div>
                        <div className="text-xs font-mono text-[hsl(var(--muted-foreground))] hidden md:block">
                            {user.email}
                        </div>
                        <button
                            data-testid="logout-button"
                            onClick={async () => {
                                await logout();
                                nav("/login", { replace: true });
                            }}
                            className="flex items-center gap-1.5 text-xs px-2.5 py-1 border border-[hsl(var(--border))] hover:border-[hsl(var(--destructive))] hover:text-[hsl(var(--destructive))] transition-colors rounded-sm"
                        >
                            <LogOut size={13} />
                            <span>Log out</span>
                        </button>
                    </div>
                </div>
            </header>
            <main className="max-w-[1400px] mx-auto px-6 py-8">
                <Outlet />
            </main>
        </div>
    );
}
