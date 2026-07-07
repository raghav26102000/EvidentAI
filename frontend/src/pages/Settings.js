import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { toast } from "sonner";
import { useAuth } from "../lib/AuthProvider";
import { Trash2, Copy, Plus, Eye, EyeOff } from "lucide-react";

function fmtDate(iso) {
    try {
        return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
    } catch {
        return iso;
    }
}

function Section({ id, title, children, testId }) {
    return (
        <section id={id} className="panel p-6 space-y-4" data-testid={testId}>
            <div className="label-caps">{title}</div>
            {children}
        </section>
    );
}

function CopyableKey({ value, testId }) {
    const [shown, setShown] = useState(false);
    return (
        <div className="flex items-center gap-2">
            <code className="font-mono text-xs bg-[hsl(var(--secondary))] border border-[hsl(var(--border))] px-2 py-1 rounded-sm truncate max-w-md" data-testid={testId}>
                {shown ? value : "•".repeat(Math.min(28, value.length))}
            </code>
            <button
                data-testid={`${testId}-toggle`}
                onClick={() => setShown((s) => !s)}
                className="text-[hsl(var(--muted-foreground))] hover:text-white"
            >
                {shown ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button
                data-testid={`${testId}-copy`}
                onClick={() => {
                    navigator.clipboard.writeText(value);
                    toast.success("Copied");
                }}
                className="text-[hsl(var(--muted-foreground))] hover:text-white"
            >
                <Copy size={14} />
            </button>
        </div>
    );
}

export default function Settings() {
    const { user, tenant } = useAuth();
    const [keys, setKeys] = useState([]);
    const [sessions, setSessions] = useState([]);
    const [newlyCreated, setNewlyCreated] = useState(null);
    const [newName, setNewName] = useState("");

    const load = async () => {
        try {
            const [k, s] = await Promise.all([api.get("/api-keys"), api.get("/sessions")]);
            setKeys(k.data);
            setSessions(s.data);
        } catch (e) {
            toast.error("Failed to load settings");
        }
    };

    useEffect(() => {
        load();
    }, []);

    const createKey = async (e) => {
        e.preventDefault();
        if (!newName.trim()) return;
        try {
            const r = await api.post("/api-keys", { name: newName.trim() });
            setNewlyCreated(r.data);
            setNewName("");
            toast.success("API key created — copy it now, it won't be shown again");
            load();
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Failed");
        }
    };

    const revokeKey = async (id) => {
        try {
            await api.delete(`/api-keys/${id}`);
            toast.success("Revoked");
            load();
        } catch (e) {
            toast.error("Failed");
        }
    };

    const revokeSession = async (id) => {
        try {
            await api.delete(`/sessions/${id}`);
            toast.success("Session revoked");
            load();
        } catch (e) {
            toast.error("Failed");
        }
    };

    return (
        <div className="space-y-6" data-testid="settings-page">
            <div>
                <div className="label-caps mb-1">Settings</div>
                <h1 className="font-heading text-3xl sm:text-4xl font-bold text-white">
                    Account & security
                </h1>
            </div>

            <Section id="profile" title="Profile" testId="settings-profile">
                <div className="grid grid-cols-2 gap-4 text-sm">
                    <Field label="Email" value={user?.email} testId="profile-email" />
                    <Field label="Role" value={user?.role} testId="profile-role" />
                    <Field label="Tenant" value={tenant?.name} testId="profile-tenant-name" />
                    <Field label="Tenant slug" value={tenant?.slug} testId="profile-tenant-slug" />
                    <Field label="User ID" value={user?.id} testId="profile-user-id" mono />
                    <Field label="Tenant ID" value={tenant?.id} testId="profile-tenant-id" mono />
                </div>
            </Section>

            <Section id="api-keys" title="API keys" testId="settings-api-keys">
                <form onSubmit={createKey} className="flex items-center gap-2" data-testid="new-api-key-form">
                    <input
                        data-testid="new-api-key-name"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        placeholder="Key name (e.g. ci-pipeline)"
                        className="flex-1 bg-[hsl(var(--input))] border border-[hsl(var(--border))] px-3 py-1.5 text-sm rounded-sm focus:outline-none focus:ring-1 focus:ring-[hsl(var(--primary))]"
                    />
                    <button
                        data-testid="create-api-key-button"
                        className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] text-white rounded-sm"
                    >
                        <Plus size={13} />
                        Create
                    </button>
                </form>

                {newlyCreated && (
                    <div className="panel p-3 space-y-2" style={{ borderColor: "hsl(158 79% 40%)" }} data-testid="new-api-key-reveal">
                        <div className="label-caps" style={{ color: "hsl(158 79% 55%)" }}>
                            One-time reveal · store securely
                        </div>
                        <CopyableKey value={newlyCreated.plaintext_key} testId="new-api-key-value" />
                        <button
                            onClick={() => setNewlyCreated(null)}
                            data-testid="new-api-key-dismiss"
                            className="text-xs text-[hsl(var(--muted-foreground))] hover:text-white"
                        >
                            Dismiss
                        </button>
                    </div>
                )}

                <div className="mt-2 border-t border-[hsl(var(--border))]">
                    {keys.length === 0 ? (
                        <div className="py-6 text-center text-xs text-[hsl(var(--muted-foreground))] font-mono">
                            No API keys yet.
                        </div>
                    ) : (
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left">
                                    <th className="label-caps py-2 font-normal">Name</th>
                                    <th className="label-caps py-2 font-normal">Prefix</th>
                                    <th className="label-caps py-2 font-normal">Created</th>
                                    <th className="label-caps py-2 font-normal">Status</th>
                                    <th></th>
                                </tr>
                            </thead>
                            <tbody data-testid="api-keys-table-body">
                                {keys.map((k) => (
                                    <tr key={k.id} className="border-t border-[hsl(var(--border))]" data-testid={`api-key-row-${k.id}`}>
                                        <td className="py-2 text-white">{k.name}</td>
                                        <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))]">{k.prefix}…</td>
                                        <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))]">{fmtDate(k.created_at)}</td>
                                        <td className="py-2 text-xs">
                                            {k.revoked ? (
                                                <span style={{ color: "hsl(0 84% 62%)" }}>revoked</span>
                                            ) : (
                                                <span style={{ color: "hsl(158 79% 55%)" }}>active</span>
                                            )}
                                        </td>
                                        <td className="py-2 text-right">
                                            {!k.revoked && (
                                                <button
                                                    data-testid={`revoke-api-key-${k.id}`}
                                                    onClick={() => revokeKey(k.id)}
                                                    className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--destructive))]"
                                                >
                                                    <Trash2 size={14} />
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </Section>

            <Section id="sessions" title="Active sessions (refresh tokens)" testId="settings-sessions">
                {sessions.length === 0 ? (
                    <div className="text-xs text-[hsl(var(--muted-foreground))] font-mono">
                        No sessions besides the current.
                    </div>
                ) : (
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left">
                                <th className="label-caps py-2 font-normal">Started</th>
                                <th className="label-caps py-2 font-normal">Expires</th>
                                <th className="label-caps py-2 font-normal">IP</th>
                                <th className="label-caps py-2 font-normal">User agent</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody data-testid="sessions-table-body">
                            {sessions.map((s) => (
                                <tr key={s.id} className="border-t border-[hsl(var(--border))]" data-testid={`session-row-${s.id}`}>
                                    <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))]">{fmtDate(s.created_at)}</td>
                                    <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))]">{fmtDate(s.expires_at)}</td>
                                    <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))]">{s.ip || "—"}</td>
                                    <td className="py-2 font-mono text-xs text-[hsl(var(--muted-foreground))] truncate max-w-md">
                                        {s.user_agent || "—"}
                                    </td>
                                    <td className="py-2 text-right">
                                        <button
                                            data-testid={`revoke-session-${s.id}`}
                                            onClick={() => revokeSession(s.id)}
                                            className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--destructive))]"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </Section>
        </div>
    );
}

function Field({ label, value, testId, mono }) {
    return (
        <div data-testid={testId}>
            <div className="label-caps">{label}</div>
            <div className={`text-white text-sm mt-1 ${mono ? "font-mono truncate" : ""}`}>{value || "—"}</div>
        </div>
    );
}
