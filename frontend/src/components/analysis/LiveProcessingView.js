import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { CheckCircle2, Loader2, Circle, ShieldAlert } from "lucide-react";

// Displays the multi-agent pipeline progression in real time while an
// insight job is running. Reads live agent_logs and paints each stage.
const STAGES = [
    { key: "profile",     label: "Profile loaded",    detail: "Column types, cardinality, null counts" },
    { key: "statistical", label: "Statistical result", detail: "Correlations, outliers, cohorts" },
    { key: "insight",     label: "Insight agent",     detail: "Ranked plain-language findings" },
    { key: "critic",      label: "Critic review",     detail: "Independent factual audit" },
];

export default function LiveProcessingView({ insightJobId, onDone }) {
    const [logs, setLogs] = useState([]);
    const [job, setJob] = useState(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            const [jr, lr] = await Promise.all([
                api.get(`/agent-jobs/${insightJobId}`),
                api.get(`/agent-jobs/${insightJobId}/logs`),
            ]);
            if (cancelled) return;
            setJob(jr.data);
            setLogs(lr.data);
            if (["pending", "running"].includes(jr.data.status)) {
                setTimeout(load, 1500);
            } else {
                onDone && onDone();
            }
        };
        load();
        return () => { cancelled = true; };
    }, [insightJobId, onDone]);

    const insightLogs = logs.filter((l) => l.agent_role === "insight");
    const criticLogs = logs.filter((l) => l.agent_role === "critic");
    const stageState = {
        profile: "done",             // Phase-1 profile is prerequisite
        statistical: "done",         // stat job was already succeeded
        insight: insightLogs.length === 0 ? "pending" : (job && job.status !== "running" ? "done" : (criticLogs.length >= insightLogs.length ? "done" : "active")),
        critic:  criticLogs.length === 0 ? (insightLogs.length === 0 ? "pending" : "active") : (job && job.status !== "running" ? "done" : "active"),
    };
    const attempt = Math.max(insightLogs.length, criticLogs.length) || 1;

    return (
        <section
            className="border border-[hsl(var(--primary))]/40 bg-[hsl(var(--card))] rounded-sm p-6"
            data-testid="live-processing-view"
        >
            <div className="flex items-baseline justify-between mb-5">
                <div>
                    <div className="label-caps text-[hsl(var(--primary))]">Pipeline running</div>
                    <h2 className="font-heading font-bold text-xl">Attempt {attempt} / 3</h2>
                </div>
                <div className="font-mono text-xs text-[hsl(var(--muted-foreground))]">
                    job {insightJobId.slice(0, 8)}
                </div>
            </div>
            <ol className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                {STAGES.map((s) => (
                    <StageCard key={s.key} state={stageState[s.key]} stage={s} />
                ))}
            </ol>
            {criticLogs.length > 0 && criticLogs[criticLogs.length - 1].payload?.verdict === "reject" && (
                <div className="mt-5 flex items-start gap-3 border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/5 rounded-sm p-4" data-testid="live-reject-banner">
                    <ShieldAlert size={16} className="text-[hsl(var(--warning))] mt-0.5" />
                    <div className="text-sm">
                        <div className="font-heading font-bold">Critic rejected attempt {criticLogs.length}, retry in progress…</div>
                        <div className="text-[hsl(var(--muted-foreground))] mt-1 font-mono text-xs">
                            {(criticLogs[criticLogs.length - 1].payload?.reasoning || "").slice(0, 220)}
                        </div>
                    </div>
                </div>
            )}
        </section>
    );
}

function StageCard({ state, stage }) {
    const Icon = state === "done" ? CheckCircle2 : state === "active" ? Loader2 : Circle;
    const color =
        state === "done"
            ? "hsl(var(--success))"
            : state === "active"
              ? "hsl(var(--primary))"
              : "hsl(var(--muted-foreground))";
    return (
        <li
            className="border border-[hsl(var(--border))] rounded-sm p-3"
            style={{ borderColor: state === "active" ? "hsl(var(--primary))" : undefined }}
            data-testid={`stage-${stage.key}`}
        >
            <div className="flex items-center gap-2">
                <Icon
                    size={14}
                    className={state === "active" ? "animate-spin" : ""}
                    style={{ color }}
                />
                <span className="label-caps" style={{ color }}>{state === "active" ? "Running" : state === "done" ? "Done" : "Waiting"}</span>
            </div>
            <div className="mt-2 text-sm font-heading font-bold">{stage.label}</div>
            <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1">{stage.detail}</div>
        </li>
    );
}
