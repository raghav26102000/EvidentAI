import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import StatusBadge from "../components/StatusBadge";
import CorrelationHeatmap from "../components/analysis/CorrelationHeatmap";
import OutlierStripPlot from "../components/analysis/OutlierStripPlot";
import ConfidenceBadge from "../components/analysis/ConfidenceBadge";
import LiveProcessingView from "../components/analysis/LiveProcessingView";
import { ChevronRight, GitBranch, ScrollText } from "lucide-react";

export default function AnalysisDashboard() {
    const { id: datasetId } = useParams();
    const nav = useNavigate();
    const [dataset, setDataset] = useState(null);
    const [jobs, setJobs] = useState([]);
    const [selectedStatJobId, setSelectedStatJobId] = useState(null);
    const [selectedInsightJobId, setSelectedInsightJobId] = useState(null);
    const [statJob, setStatJob] = useState(null);
    const [insightJob, setInsightJob] = useState(null);
    const [triggering, setTriggering] = useState(false);

    // load dataset + job list
    useEffect(() => {
        (async () => {
            const dsResp = await api.get(`/datasets/${datasetId}`).catch(() => null);
            if (dsResp) setDataset(dsResp.data);
            const jobsResp = await api.get(`/datasets/${datasetId}/agent-jobs`);
            setJobs(jobsResp.data);
            const firstStat = jobsResp.data.find(
                (j) => j.agent_type === "statistical" && j.status === "succeeded",
            );
            if (firstStat) setSelectedStatJobId(firstStat.id);
            const firstInsight = jobsResp.data.find((j) => j.agent_type === "insight");
            if (firstInsight) setSelectedInsightJobId(firstInsight.id);
        })();
    }, [datasetId]);

    // load selected stat job detail
    useEffect(() => {
        if (!selectedStatJobId) { setStatJob(null); return; }
        api.get(`/agent-jobs/${selectedStatJobId}`).then((r) => setStatJob(r.data));
    }, [selectedStatJobId]);

    // load selected insight job detail, poll if running
    useEffect(() => {
        if (!selectedInsightJobId) { setInsightJob(null); return; }
        let cancelled = false;
        const load = async () => {
            const r = await api.get(`/agent-jobs/${selectedInsightJobId}`);
            if (cancelled) return;
            setInsightJob(r.data);
            if (["pending", "running"].includes(r.data.status)) {
                setTimeout(load, 2000);
            }
        };
        load();
        return () => { cancelled = true; };
    }, [selectedInsightJobId]);

    const statResult = statJob?.payload?.result || {};
    const findings = insightJob?.payload?.final_insights?.findings || [];
    const confidence = insightJob?.payload?.confidence;
    const insightLoading =
        insightJob && ["pending", "running"].includes(insightJob.status);

    const triggerInsights = async (forceOverstate = false) => {
        if (!selectedStatJobId) return;
        setTriggering(true);
        try {
            const r = await api.post(`/datasets/${datasetId}/insights`, {
                stat_job_id: selectedStatJobId,
                test_force_overstate_first_attempt: forceOverstate,
            });
            const nextJobs = await api.get(`/datasets/${datasetId}/agent-jobs`);
            setJobs(nextJobs.data);
            setSelectedInsightJobId(r.data.id);
        } finally { setTriggering(false); }
    };

    return (
        <div className="space-y-8" data-testid="analysis-dashboard">
            {/* Header */}
            <div className="flex items-start justify-between gap-4 border-b border-[hsl(var(--border))] pb-6">
                <div>
                    <div className="label-caps text-[hsl(var(--muted-foreground))] mb-2">
                        Analysis / Dataset
                    </div>
                    <h1 className="font-heading font-bold text-3xl tracking-tight">
                        {dataset?.original_filename || datasetId?.slice(0, 8)}
                    </h1>
                    <div className="font-mono text-xs text-[hsl(var(--muted-foreground))] mt-1">
                        {datasetId}
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        data-testid="btn-run-insights"
                        disabled={!selectedStatJobId || triggering}
                        onClick={() => triggerInsights(false)}
                        className="text-xs px-3 py-1.5 border border-[hsl(var(--border))] hover:border-[hsl(var(--primary))] hover:text-[hsl(var(--primary))] rounded-sm transition-colors disabled:opacity-40"
                    >
                        {triggering ? "Starting..." : "Run Insight Pipeline"}
                    </button>
                    <button
                        data-testid="btn-run-insights-demo"
                        disabled={!selectedStatJobId || triggering}
                        onClick={() => triggerInsights(true)}
                        title="Runs with forced overstatement on attempt 1 to demonstrate the reject/retry trace"
                        className="text-xs px-3 py-1.5 border border-[hsl(var(--warning))]/40 text-[hsl(var(--warning))] hover:border-[hsl(var(--warning))] rounded-sm transition-colors disabled:opacity-40"
                    >
                        Run demo (force overstate)
                    </button>
                    {selectedInsightJobId && (
                        <button
                            data-testid="btn-open-trail"
                            onClick={() => nav(`/agent-trail/${selectedInsightJobId}`)}
                            className="text-xs px-3 py-1.5 bg-[hsl(var(--primary))] text-white hover:bg-[hsl(var(--primary))]/90 rounded-sm inline-flex items-center gap-1.5"
                        >
                            <GitBranch size={12} /> Open agent trail <ChevronRight size={12} />
                        </button>
                    )}
                </div>
            </div>

            {/* Job selector row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <JobPicker
                    label="Statistical job"
                    jobs={jobs.filter((j) => j.agent_type === "statistical")}
                    selectedId={selectedStatJobId}
                    onSelect={setSelectedStatJobId}
                    testId="stat-job-picker"
                />
                <JobPicker
                    label="Insight job"
                    jobs={jobs.filter((j) => j.agent_type === "insight")}
                    selectedId={selectedInsightJobId}
                    onSelect={setSelectedInsightJobId}
                    testId="insight-job-picker"
                    onOpenTrail={(id) => nav(`/agent-trail/${id}`)}
                />
            </div>

            {/* Live processing view — shown only while insight job is running */}
            {insightLoading && (
                <LiveProcessingView
                    insightJobId={selectedInsightJobId}
                    onDone={() => api.get(`/agent-jobs/${selectedInsightJobId}`).then((r) => setInsightJob(r.data))}
                />
            )}

            {/* Main two-column layout: insights left, charts right */}
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                {/* Insights panel */}
                <section
                    className="lg:col-span-3 border border-[hsl(var(--border))] bg-[hsl(var(--card))] rounded-sm p-6"
                    data-testid="insights-panel"
                >
                    <div className="flex items-center justify-between mb-6">
                        <div>
                            <div className="label-caps text-[hsl(var(--muted-foreground))]">
                                Findings
                            </div>
                            <h2 className="font-heading font-bold text-xl">
                                Ranked Insights
                            </h2>
                        </div>
                        {confidence && <ConfidenceBadge confidence={confidence} />}
                    </div>

                    {findings.length === 0 && !insightLoading && (
                        <EmptyState label="No insights yet. Run the insight pipeline to generate a ranked write-up." />
                    )}
                    {findings.length === 0 && insightLoading && (
                        <EmptyState label="Waiting for the insight agent to produce findings..." />
                    )}

                    <ol className="space-y-3">
                        {findings.map((f, idx) => (
                            <RankedFinding key={idx} finding={f} rank={idx + 1} total={findings.length} />
                        ))}
                    </ol>
                </section>

                {/* Chart panel */}
                <section className="lg:col-span-2 space-y-6">
                    <div className="border border-[hsl(var(--border))] bg-[hsl(var(--card))] rounded-sm p-5">
                        <div className="label-caps text-[hsl(var(--muted-foreground))] mb-3">
                            Correlation matrix
                        </div>
                        <CorrelationHeatmap data={statResult.pearson_correlation} />
                    </div>
                    <div className="border border-[hsl(var(--border))] bg-[hsl(var(--card))] rounded-sm p-5">
                        <div className="label-caps text-[hsl(var(--muted-foreground))] mb-3">
                            IQR outliers
                        </div>
                        <OutlierStripPlot data={statResult.iqr_outliers} />
                    </div>
                </section>
            </div>
        </div>
    );
}

function JobPicker({ label, jobs, selectedId, onSelect, testId, onOpenTrail }) {
    return (
        <div className="border border-[hsl(var(--border))] bg-[hsl(var(--card))] rounded-sm" data-testid={testId}>
            <div className="px-4 py-3 border-b border-[hsl(var(--border))] flex items-center justify-between">
                <span className="label-caps text-[hsl(var(--muted-foreground))]">{label}</span>
                <span className="text-xs font-mono text-[hsl(var(--muted-foreground))]">{jobs.length} jobs</span>
            </div>
            <ul className="divide-y divide-[hsl(var(--border))]">
                {jobs.length === 0 && (
                    <li className="px-4 py-4 text-sm text-[hsl(var(--muted-foreground))]">No jobs yet.</li>
                )}
                {jobs.slice(0, 6).map((j) => (
                    <li
                        key={j.id}
                        className={`px-4 py-2.5 flex items-center gap-3 cursor-pointer transition-colors ${
                            selectedId === j.id
                                ? "bg-[hsl(var(--secondary))]"
                                : "hover:bg-[hsl(var(--secondary))]"
                        }`}
                        onClick={() => onSelect(j.id)}
                        data-testid={`${testId}-row`}
                    >
                        <span className="font-mono text-xs text-[hsl(var(--muted-foreground))] w-16 truncate">
                            {j.id.slice(0, 8)}
                        </span>
                        <StatusBadge status={j.status} />
                        <span className="text-xs text-[hsl(var(--muted-foreground))] font-mono flex-1">
                            {new Date(j.created_at).toLocaleString()}
                        </span>
                        <span className="text-xs font-mono text-[hsl(var(--muted-foreground))]">
                            {j.cost_tokens} tok
                        </span>
                        {onOpenTrail && (
                            <button
                                onClick={(e) => { e.stopPropagation(); onOpenTrail(j.id); }}
                                className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--primary))]"
                                title="Open trail"
                            >
                                <ScrollText size={13} />
                            </button>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
}

function RankedFinding({ finding, rank, total }) {
    // Visual weighting: rank 1 largest, taper down.
    const emphasis =
        rank === 1
            ? "text-2xl leading-tight"
            : rank === 2
              ? "text-lg leading-snug"
              : rank === 3
                ? "text-base leading-snug"
                : "text-sm leading-snug";
    const rankColor =
        rank === 1
            ? "text-white"
            : rank <= 3
              ? "text-[hsl(var(--foreground))]"
              : "text-[hsl(var(--muted-foreground))]";
    return (
        <li className="grid grid-cols-[3rem_1fr] gap-4 border-l border-[hsl(var(--border))] pl-4">
            <div className="font-mono text-xs pt-1 text-[hsl(var(--muted-foreground))]">
                #{String(rank).padStart(2, "0")}
            </div>
            <div>
                <div className={`font-heading font-bold tracking-tight ${emphasis} ${rankColor}`}>
                    {finding.title || finding.claim}
                </div>
                {finding.claim && finding.title && finding.claim !== finding.title && (
                    <div className="text-sm text-[hsl(var(--muted-foreground))] mt-1 leading-relaxed">
                        {finding.claim}
                    </div>
                )}
                <div className="flex items-center gap-3 mt-2 text-xs font-mono text-[hsl(var(--muted-foreground))]">
                    <span>importance {(finding.importance_score ?? 0).toFixed(2)}</span>
                    <span className="opacity-40">·</span>
                    <span>{(finding.backed_by || []).join(", ")}</span>
                </div>
            </div>
        </li>
    );
}

function EmptyState({ label }) {
    return (
        <div className="font-mono text-sm text-[hsl(var(--muted-foreground))] py-8 border border-dashed border-[hsl(var(--border))] rounded-sm px-4 text-center">
            {label}
        </div>
    );
}
