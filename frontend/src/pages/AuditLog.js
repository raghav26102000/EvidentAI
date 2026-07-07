import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { RefreshCcw } from "lucide-react";

function fmtDate(iso) {
    try {
        return new Date(iso).toISOString().replace("T", " ").slice(0, 19) + "Z";
    } catch {
        return iso;
    }
}

const EVENT_COLOR = {
    "auth.login": "hsl(158,79%,55%)",
    "auth.logout": "hsl(240,5%,70%)",
    "auth.refresh_reuse_detected": "hsl(0,84%,62%)",
    "tenant.created": "hsl(217,91%,60%)",
    "dataset.uploaded": "hsl(217,91%,60%)",
    "dataset.profiled": "hsl(158,79%,55%)",
    "dataset.rejected": "hsl(0,84%,62%)",
    "session.revoked": "hsl(38,92%,55%)",
    "api_key.created": "hsl(217,91%,60%)",
    "api_key.revoked": "hsl(38,92%,55%)",
};

export default function AuditLog() {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState(null);

    const load = async () => {
        setLoading(true);
        setErr(null);
        try {
            const r = await api.get("/audit");
            setRows(r.data);
        } catch (e) {
            setErr(e?.response?.data?.detail || "Failed");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    return (
        <div className="space-y-6" data-testid="audit-page">
            <div className="flex items-end justify-between flex-wrap gap-4">
                <div>
                    <div className="label-caps mb-1">Audit</div>
                    <h1 className="font-heading text-3xl sm:text-4xl font-bold text-white">
                        Immutable event log
                    </h1>
                    <p className="text-sm text-[hsl(var(--muted-foreground))] mt-1 font-mono max-w-2xl">
                        Every security-relevant action lands here — auth, uploads, key rotation,
                        session revocation, refresh-token reuse. Scoped to your tenant by RLS.
                    </p>
                </div>
                <button
                    data-testid="refresh-audit-button"
                    onClick={load}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 border border-[hsl(var(--border))] hover:bg-[hsl(var(--secondary))] rounded-sm"
                >
                    <RefreshCcw size={13} />
                    Refresh
                </button>
            </div>

            <div className="panel overflow-hidden">
                <table className="w-full text-sm font-mono">
                    <thead className="bg-[hsl(var(--secondary))]">
                        <tr className="text-left">
                            <th className="label-caps px-4 py-2.5 font-normal">Timestamp (UTC)</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Event</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Resource</th>
                            <th className="label-caps px-4 py-2.5 font-normal">IP</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Metadata</th>
                        </tr>
                    </thead>
                    <tbody data-testid="audit-table-body">
                        {loading ? (
                            <tr>
                                <td colSpan={5} className="px-4 py-8 text-center text-[hsl(var(--muted-foreground))]">loading…</td>
                            </tr>
                        ) : err ? (
                            <tr>
                                <td colSpan={5} className="px-4 py-6 text-center text-[hsl(var(--destructive))]">{err}</td>
                            </tr>
                        ) : rows.length === 0 ? (
                            <tr>
                                <td colSpan={5} className="px-4 py-16 text-center text-[hsl(var(--muted-foreground))] text-xs">
                                    No events yet.
                                </td>
                            </tr>
                        ) : (
                            rows.map((r) => (
                                <tr key={r.id} className="border-t border-[hsl(var(--border))]" data-testid={`audit-row-${r.id}`}>
                                    <td className="px-4 py-2 text-xs text-[hsl(var(--muted-foreground))] tabular-nums">
                                        {fmtDate(r.created_at)}
                                    </td>
                                    <td className="px-4 py-2 text-xs">
                                        <span style={{ color: EVENT_COLOR[r.event_type] || "hsl(0 0% 90%)" }}>
                                            {r.event_type}
                                        </span>
                                    </td>
                                    <td className="px-4 py-2 text-xs text-[hsl(var(--muted-foreground))]">
                                        {r.resource_type ? `${r.resource_type}:${(r.resource_id || "").slice(0, 8)}` : "—"}
                                    </td>
                                    <td className="px-4 py-2 text-xs text-[hsl(var(--muted-foreground))]">{r.ip || "—"}</td>
                                    <td className="px-4 py-2 text-xs text-[hsl(var(--muted-foreground))] truncate max-w-xs">
                                        {Object.keys(r.metadata_json || r.metadata || {}).length
                                            ? JSON.stringify(r.metadata_json || r.metadata)
                                            : "—"}
                                    </td>
                                </tr>
                            ))
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
