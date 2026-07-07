import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import StatusBadge from "../components/StatusBadge";
import { ArrowLeft, FileText } from "lucide-react";
import { BarChart, Bar, ResponsiveContainer, Tooltip } from "recharts";

function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    if (typeof n === "number") {
        if (Math.abs(n) >= 10000 || (Math.abs(n) < 0.01 && n !== 0)) return n.toExponential(2);
        return Number.isInteger(n) ? n.toString() : n.toFixed(3);
    }
    return String(n);
}

function Sparkline({ hist }) {
    if (!hist || hist.length === 0) return <span className="text-[hsl(var(--muted-foreground))] font-mono text-xs">—</span>;
    const data = hist.map((c, i) => ({ i, c }));
    return (
        <div style={{ width: 120, height: 28 }}>
            <ResponsiveContainer>
                <BarChart data={data}>
                    <Tooltip
                        cursor={{ fill: "hsl(240 4% 16% / 0.4)" }}
                        contentStyle={{
                            background: "hsl(240 10% 3.9%)",
                            border: "1px solid hsl(240 4% 16%)",
                            fontFamily: "JetBrains Mono, monospace",
                            fontSize: 11,
                        }}
                        labelFormatter={(v) => `bin ${v}`}
                    />
                    <Bar dataKey="c" fill="hsl(217 100% 50%)" radius={0} />
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}

function TypeChip({ type }) {
    const map = {
        numeric: { c: "hsl(217,91%,60%)", bg: "hsl(217 91% 40% / 0.10)", br: "hsl(217 91% 40% / 0.35)" },
        text: { c: "hsl(280,60%,70%)", bg: "hsl(280 60% 40% / 0.10)", br: "hsl(280 60% 40% / 0.35)" },
        boolean: { c: "hsl(158,79%,55%)", bg: "hsl(158 79% 40% / 0.10)", br: "hsl(158 79% 40% / 0.35)" },
        datetime: { c: "hsl(38,92%,55%)", bg: "hsl(38 92% 40% / 0.10)", br: "hsl(38 92% 40% / 0.35)" },
        mixed: { c: "hsl(0,60%,70%)", bg: "hsl(0 60% 40% / 0.10)", br: "hsl(0 60% 40% / 0.35)" },
    };
    const p = map[type] || map.mixed;
    return (
        <span
            className="badge"
            style={{ color: p.c, background: p.bg, borderColor: p.br }}
        >
            {type}
        </span>
    );
}

export default function DatasetDetail() {
    const { id } = useParams();
    const [ds, setDs] = useState(null);
    const [profile, setProfile] = useState(null);
    const [err, setErr] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        (async () => {
            setLoading(true);
            try {
                const r = await api.get(`/datasets/${id}`);
                if (!alive) return;
                setDs(r.data);
                if (r.data.status === "ready") {
                    const p = await api.get(`/datasets/${id}/profile`);
                    if (!alive) return;
                    setProfile(p.data);
                }
            } catch (e) {
                setErr(e?.response?.data?.detail || "Failed to load");
            } finally {
                if (alive) setLoading(false);
            }
        })();
        return () => {
            alive = false;
        };
    }, [id]);

    if (loading) return <div className="font-mono text-sm text-[hsl(var(--muted-foreground))]">loading…</div>;
    if (err) return <div className="text-[hsl(var(--destructive))] text-sm">{err}</div>;
    if (!ds) return null;

    return (
        <div className="space-y-8" data-testid="dataset-detail-page">
            <div>
                <Link
                    to="/datasets"
                    data-testid="back-to-datasets"
                    className="inline-flex items-center gap-1 text-xs text-[hsl(var(--muted-foreground))] hover:text-white"
                >
                    <ArrowLeft size={12} />
                    All datasets
                </Link>
                <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2 label-caps">
                            <FileText size={12} />
                            <span data-testid="dataset-filename">{ds.original_filename}</span>
                        </div>
                        <h1 className="font-heading text-3xl font-bold text-white mt-1 font-mono tabular-nums">
                            {ds.id.slice(0, 8)}…
                        </h1>
                    </div>
                    <StatusBadge status={ds.status} testId="dataset-status-badge" />
                </div>
                <div className="mt-3">
                    <Link
                        to={`/datasets/${ds.id}/analysis`}
                        data-testid="link-open-analysis"
                        className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 bg-[hsl(var(--primary))] text-white hover:bg-[hsl(var(--primary))]/90 rounded-sm"
                    >
                        Open analysis dashboard →
                    </Link>
                </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <MetricCard label="Size" value={`${ds.size_bytes} B`} testId="meta-size" />
                <MetricCard label="Content-Type" value={ds.content_type} testId="meta-type" />
                <MetricCard label="Scan" value={ds.scan_result || "—"} testId="meta-scan" />
                <MetricCard label="Rows" value={profile ? profile.row_count : "—"} testId="meta-rows" />
                <MetricCard label="Columns" value={profile ? profile.column_count : "—"} testId="meta-cols" />
            </div>

            {ds.status === "failed" && (
                <div
                    className="panel p-4"
                    style={{ borderColor: "hsl(0 84% 40%)", color: "hsl(0 84% 72%)" }}
                    data-testid="dataset-error"
                >
                    <div className="label-caps mb-1">Error</div>
                    <div className="font-mono text-xs">{ds.error_message || "Unknown"}</div>
                </div>
            )}

            {profile && (
                <div className="space-y-3">
                    <div className="label-caps">Column profile · deterministic</div>
                    <div className="panel overflow-hidden">
                        <table className="w-full text-sm">
                            <thead className="bg-[hsl(var(--secondary))]">
                                <tr className="text-left">
                                    <th className="label-caps px-4 py-2.5 font-normal">Column</th>
                                    <th className="label-caps px-4 py-2.5 font-normal">Type</th>
                                    <th className="label-caps px-4 py-2.5 font-normal text-right">Nulls</th>
                                    <th className="label-caps px-4 py-2.5 font-normal text-right">Cardinality</th>
                                    <th className="label-caps px-4 py-2.5 font-normal text-right">Min</th>
                                    <th className="label-caps px-4 py-2.5 font-normal text-right">Mean</th>
                                    <th className="label-caps px-4 py-2.5 font-normal text-right">Max</th>
                                    <th className="label-caps px-4 py-2.5 font-normal">Distribution</th>
                                </tr>
                            </thead>
                            <tbody data-testid="columns-table-body">
                                {profile.columns.map((c) => (
                                    <tr
                                        key={c.name}
                                        className="border-t border-[hsl(var(--border))]"
                                        data-testid={`column-row-${c.name}`}
                                    >
                                        <td className="px-4 py-2.5 font-mono text-white">{c.name}</td>
                                        <td className="px-4 py-2.5"><TypeChip type={c.type} /></td>
                                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                            {c.null_count}
                                        </td>
                                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                            {c.cardinality ?? "—"}
                                        </td>
                                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                            {c.type === "numeric" ? fmtNum(c.stats?.min) : "—"}
                                        </td>
                                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                            {c.type === "numeric" ? fmtNum(c.stats?.mean) : "—"}
                                        </td>
                                        <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[hsl(var(--muted-foreground))]">
                                            {c.type === "numeric" ? fmtNum(c.stats?.max) : "—"}
                                        </td>
                                        <td className="px-4 py-2.5">
                                            {c.type === "numeric" ? (
                                                <Sparkline hist={c.stats?.histogram} />
                                            ) : (
                                                <span className="font-mono text-xs text-[hsl(var(--muted-foreground))]">
                                                    {(c.top_values || [])
                                                        .slice(0, 2)
                                                        .map((t) => `${String(t.value).slice(0, 10)}×${t.count}`)
                                                        .join("  ")}
                                                </span>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}

function MetricCard({ label, value, testId }) {
    return (
        <div className="panel p-3" data-testid={testId}>
            <div className="label-caps">{label}</div>
            <div className="font-mono text-white text-sm mt-1 truncate">{String(value)}</div>
        </div>
    );
}
