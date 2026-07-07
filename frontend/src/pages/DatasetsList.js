import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import StatusBadge from "../components/StatusBadge";
import { Upload, RefreshCcw, FileText, ChevronRight, Sparkles } from "lucide-react";

const DEMO_FILENAME = "example-correlation-review.csv";
function isDemo(row) {
    return row?.original_filename === DEMO_FILENAME;
}

function fmtBytes(n) {
    if (!n && n !== 0) return "—";
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    let v = Number(n);
    while (v >= 1024 && i < u.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtDate(iso) {
    try {
        const d = new Date(iso);
        return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
    } catch {
        return iso;
    }
}

export default function DatasetsList() {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [err, setErr] = useState(null);

    const load = async () => {
        setLoading(true);
        setErr(null);
        try {
            const r = await api.get("/datasets");
            setRows(r.data);
        } catch (e) {
            setErr(e?.response?.data?.detail || "Failed to load");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    return (
        <div className="space-y-6" data-testid="datasets-page">
            {/* First-time-viewer callout: only render if a demo row exists */}
            {rows.some(isDemo) && (() => {
                const demo = rows.find(isDemo);
                return (
                    <Link
                        to={`/datasets/${demo.id}/analysis`}
                        data-testid="demo-callout"
                        className="block border border-[hsl(var(--primary))]/50 bg-gradient-to-r from-[hsl(var(--primary))]/10 to-transparent rounded-sm p-5 hover:border-[hsl(var(--primary))] transition-colors group"
                    >
                        <div className="flex items-start gap-4">
                            <Sparkles size={20} className="text-[hsl(var(--primary))] mt-0.5 flex-shrink-0" />
                            <div className="flex-1">
                                <div className="label-caps text-[hsl(var(--primary))] mb-1">New here? Start with the example</div>
                                <div className="font-heading font-bold text-white text-lg leading-snug">
                                    Example analysis · correlation review with agent critique
                                </div>
                                <div className="text-sm text-[hsl(var(--muted-foreground))] mt-1.5 max-w-3xl">
                                    A finished statistical + insight run showing the multi-agent pipeline
                                    catching an overstated correlation, sending it back for revision, and
                                    approving the corrected write-up on retry.
                                </div>
                            </div>
                            <div className="flex items-center gap-1 text-xs text-[hsl(var(--primary))] font-mono opacity-70 group-hover:opacity-100 self-center">
                                Open <ChevronRight size={14} />
                            </div>
                        </div>
                    </Link>
                );
            })()}

            <div className="flex items-end justify-between gap-4 flex-wrap">
                <div>
                    <div className="label-caps mb-1">Datasets</div>
                    <h1 className="font-heading text-3xl sm:text-4xl font-bold text-white">
                        Uploaded files
                    </h1>
                    <p className="text-sm text-[hsl(var(--muted-foreground))] mt-1 max-w-2xl">
                        Every file is scanned, hardened, encrypted with your tenant key, then
                        profiled in a sandboxed subprocess with no network. Only ciphertext is
                        stored.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        data-testid="refresh-datasets-button"
                        onClick={load}
                        className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 border border-[hsl(var(--border))] hover:bg-[hsl(var(--secondary))] rounded-sm"
                    >
                        <RefreshCcw size={13} />
                        Refresh
                    </button>
                    <Link
                        to="/upload"
                        data-testid="new-upload-link"
                        className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] text-white rounded-sm"
                    >
                        <Upload size={13} />
                        New upload
                    </Link>
                </div>
            </div>

            <div className="panel overflow-hidden">
                <table className="w-full text-sm">
                    <thead className="bg-[hsl(var(--secondary))]">
                        <tr className="text-left">
                            <th className="label-caps px-4 py-2.5 font-normal">Filename</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Status</th>
                            <th className="label-caps px-4 py-2.5 font-normal text-right">Size</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Uploaded</th>
                            <th className="label-caps px-4 py-2.5 font-normal">Scan</th>
                            <th className="px-4 py-2.5"></th>
                        </tr>
                    </thead>
                    <tbody data-testid="datasets-table-body">
                        {loading ? (
                            <tr>
                                <td colSpan={6} className="px-4 py-12 text-center text-[hsl(var(--muted-foreground))] text-sm">
                                    <div className="font-mono">loading…</div>
                                </td>
                            </tr>
                        ) : err ? (
                            <tr>
                                <td colSpan={6} className="px-4 py-8 text-center text-[hsl(var(--destructive))] text-sm">
                                    {err}
                                </td>
                            </tr>
                        ) : rows.length === 0 ? (
                            <tr>
                                <td colSpan={6} className="px-4 py-16 text-center">
                                    <pre className="font-mono text-[hsl(var(--muted-foreground))] text-xs leading-tight mx-auto inline-block text-left">{`
    ┌─ EMPTY ──────────────────┐
    │ 0 datasets yet.          │
    │ Upload a CSV or .xlsx    │
    │ to see profiling output. │
    └──────────────────────────┘`}</pre>
                                    <div className="mt-6">
                                        <Link
                                            to="/upload"
                                            data-testid="empty-upload-link"
                                            className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-[hsl(var(--primary))] hover:bg-[hsl(217,100%,45%)] text-white rounded-sm"
                                        >
                                            <Upload size={13} />
                                            Upload your first file
                                        </Link>
                                    </div>
                                </td>
                            </tr>
                        ) : (
                            rows.map((r) => {
                                const demo = isDemo(r);
                                const detailHref = demo ? `/datasets/${r.id}/analysis` : `/datasets/${r.id}`;
                                return (
                                <tr
                                    key={r.id}
                                    className={`border-t border-[hsl(var(--border))] hover:bg-[hsl(var(--secondary))] transition-colors ${demo ? "bg-[hsl(var(--primary))]/5" : ""}`}
                                    data-testid={`dataset-row-${r.id}`}
                                >
                                    <td className="px-4 py-2.5">
                                        <Link
                                            to={detailHref}
                                            data-testid={`dataset-link-${r.id}`}
                                            className="flex items-center gap-2 text-white hover:text-[hsl(var(--primary))]"
                                        >
                                            <FileText size={14} strokeWidth={1.5} className="text-[hsl(var(--muted-foreground))]" />
                                            <span className="font-mono text-sm">{r.original_filename}</span>
                                            {demo && (
                                                <span
                                                    data-testid="demo-pill"
                                                    className="ml-1 text-[10px] font-mono px-1.5 py-0.5 rounded-sm border border-[hsl(var(--primary))]/50 text-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10"
                                                >
                                                    EXAMPLE
                                                </span>
                                            )}
                                        </Link>
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <StatusBadge status={r.status} testId={`row-status-${r.id}`} />
                                    </td>
                                    <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                        {fmtBytes(r.size_bytes)}
                                    </td>
                                    <td className="px-4 py-2.5 font-mono tabular-nums text-xs text-[hsl(var(--muted-foreground))]">
                                        {fmtDate(r.created_at)}
                                    </td>
                                    <td className="px-4 py-2.5">
                                        <StatusBadge status={r.scan_result} testId={`row-scan-${r.id}`} />
                                    </td>
                                    <td className="px-4 py-2.5 text-right">
                                        <Link
                                            to={detailHref}
                                            className="inline-flex text-[hsl(var(--muted-foreground))] hover:text-white"
                                        >
                                            <ChevronRight size={16} />
                                        </Link>
                                    </td>
                                </tr>
                                );
                            })
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
