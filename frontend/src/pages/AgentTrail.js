import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import StatusBadge from "../components/StatusBadge";
import ConfidenceBadge from "../components/analysis/ConfidenceBadge";
import {
    ArrowLeft, ArrowRight, ShieldCheck, ShieldAlert,
    Wand2, FileWarning, RefreshCcw, Cpu,
} from "lucide-react";

// AGENT TRAIL VIEW — Phase 4 centerpiece screen.
// Shows the sequence of agent actions with the reject/retry cycle rendered
// as a clear before/critic-verdict/after triptych.
export default function AgentTrail() {
    const { jobId } = useParams();
    const nav = useNavigate();
    const [job, setJob] = useState(null);
    const [logs, setLogs] = useState([]);
    const [decisions, setDecisions] = useState([]);

    useEffect(() => {
        (async () => {
            const [j, l, d] = await Promise.all([
                api.get(`/agent-jobs/${jobId}`),
                api.get(`/agent-jobs/${jobId}/logs`),
                api.get(`/agent-jobs/${jobId}/critic-decisions`),
            ]);
            setJob(j.data);
            setLogs(l.data);
            setDecisions(d.data);
        })();
    }, [jobId]);

    if (!job) return null;

    // Group logs by attempt number (extracted from step: "attempt_1", "attempt_2", ...)
    const attempts = groupByAttempt(logs, decisions, job);
    const confidence = job.payload?.confidence;
    const totalAttempts = attempts.length;
    const wasRejected = attempts.some(
        (a) => a.critic && (a.critic.payload?.verdict === "reject"),
    );

    return (
        <div className="space-y-8" data-testid="agent-trail-view">
            {/* Header */}
            <div className="flex items-start justify-between border-b border-[hsl(var(--border))] pb-6">
                <div>
                    <button
                        onClick={() => nav(-1)}
                        className="flex items-center gap-1.5 text-xs text-[hsl(var(--muted-foreground))] hover:text-white mb-3"
                        data-testid="trail-back"
                    >
                        <ArrowLeft size={12} /> Back
                    </button>
                    <div className="label-caps text-[hsl(var(--muted-foreground))] mb-1">Agent trail</div>
                    <h1 className="font-heading font-bold text-3xl tracking-tight">
                        {job.agent_type === "insight" ? "Insight generation" : "Statistical analysis"} job
                    </h1>
                    <div className="font-mono text-xs text-[hsl(var(--muted-foreground))] mt-1">
                        {jobId}
                    </div>
                </div>
                <div className="flex flex-col items-end gap-2 text-right">
                    <StatusBadge status={job.status} />
                    {confidence && <ConfidenceBadge confidence={confidence} />}
                    <div className="font-mono text-xs text-[hsl(var(--muted-foreground))]">
                        {totalAttempts} attempt{totalAttempts === 1 ? "" : "s"} · {job.cost_tokens || 0} tokens
                    </div>
                </div>
            </div>

            {/* One-line summary — must be legible in under 10 seconds */}
            <TrailSummary job={job} attempts={attempts} wasRejected={wasRejected} />

            {/* Attempt timeline — each attempt is a triptych */}
            <div className="space-y-6">
                {attempts.map((att, idx) => (
                    <AttemptCard
                        key={idx}
                        attempt={att}
                        idx={idx}
                        prevAttempt={attempts[idx - 1]}
                        nextAttempt={attempts[idx + 1]}
                        isFinal={idx === attempts.length - 1}
                    />
                ))}
            </div>
        </div>
    );
}

function groupByAttempt(logs, decisions, job) {
    const byNum = {};
    for (const l of logs) {
        const m = /attempt_(\d+)/.exec(l.step || "");
        if (!m) continue;
        const n = Number(m[1]);
        byNum[n] = byNum[n] || { n };
        if (l.agent_role === "insight") byNum[n].insight = l;
        else if (l.agent_role === "critic") byNum[n].critic = l;
        else if (l.agent_role === "orchestrator") byNum[n].orchestrator = l;
    }
    const decisionsByStamp = decisions.map((d, i) => ({ ...d, order: i }));
    // pair decisions to attempts by order (critic logs and decisions are 1:1 by design)
    const attemptList = Object.values(byNum).sort((a, b) => a.n - b.n);
    attemptList.forEach((a, i) => {
        if (decisionsByStamp[i]) a.decision = decisionsByStamp[i];
    });
    // Pull findings for each attempt from the job payload if present
    const jobAttempts = job.payload?.attempts || [];
    attemptList.forEach((a, i) => {
        if (jobAttempts[i]) a.findings = jobAttempts[i].insight_findings;
    });
    return attemptList;
}

function TrailSummary({ job, attempts, wasRejected }) {
    const first = attempts[0];
    const last = attempts[attempts.length - 1];
    const parentStat = job.payload?.parent_stat_job_id;
    return (
        <div className="border border-[hsl(var(--border))] bg-[hsl(var(--card))] rounded-sm p-5" data-testid="trail-summary">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <SummaryCell icon={Cpu} label="Parent statistical job" value={parentStat ? parentStat.slice(0, 12) + "…" : "—"} />
                <SummaryCell icon={Wand2} label="Insight attempts" value={`${attempts.length} / ${job.payload?.max_attempts || 3}`} />
                <SummaryCell
                    icon={wasRejected ? ShieldAlert : ShieldCheck}
                    label="Critic outcome"
                    value={last?.critic?.payload?.verdict === "approve" ? "Approved" : (wasRejected ? "Approved after retry" : "Blocked")}
                    tone={last?.critic?.payload?.verdict === "approve" ? "success" : (wasRejected ? "warning" : "critical")}
                />
                <SummaryCell icon={RefreshCcw} label="Retries used" value={String(Math.max(0, attempts.length - 1))} />
            </div>
            {wasRejected && (
                <div className="mt-5 text-sm leading-relaxed">
                    <span className="text-[hsl(var(--muted-foreground))]">Story: </span>
                    <span className="text-white">
                        The insight agent's <span className="text-[hsl(var(--warning))] font-medium">attempt {first.n}</span> was
                        {" "}rejected by the critic for {(first.critic?.payload?.issues?.[0]?.problem || "an overstated claim").toLowerCase()}.
                        The specific objection was routed back to the insight agent, which produced a corrected write-up on
                        {" "}<span className="text-[hsl(var(--success))] font-medium">attempt {last.n}</span>. The critic then approved.
                    </span>
                </div>
            )}
        </div>
    );
}

function SummaryCell({ icon: Icon, label, value, tone }) {
    const color =
        tone === "success" ? "hsl(var(--success))"
      : tone === "warning" ? "hsl(var(--warning))"
      : tone === "critical" ? "hsl(var(--destructive))"
      : "hsl(var(--foreground))";
    return (
        <div>
            <div className="flex items-center gap-1.5 label-caps text-[hsl(var(--muted-foreground))] mb-1.5">
                <Icon size={11} /> {label}
            </div>
            <div className="font-heading font-bold text-lg tracking-tight" style={{ color }}>
                {value}
            </div>
        </div>
    );
}

function AttemptCard({ attempt, idx, prevAttempt, nextAttempt, isFinal }) {
    const verdict = attempt.critic?.payload?.verdict; // "approve" | "reject"
    const criticIssues = attempt.critic?.payload?.issues || [];
    const findings = attempt.findings?.findings || [];
    const forceOverstate = attempt.insight?.payload?.force_overstate_active;
    const isRejected = verdict === "reject";
    return (
        <section
            className={`border rounded-sm overflow-hidden ${isRejected ? "border-[hsl(var(--warning))]/40" : "border-[hsl(var(--border))]"}`}
            data-testid={`attempt-${attempt.n}`}
        >
            <div className="flex items-center justify-between px-5 py-3 bg-[hsl(var(--card))] border-b border-[hsl(var(--border))]">
                <div className="flex items-center gap-3">
                    <div className="font-mono text-xs px-2 py-0.5 border border-[hsl(var(--border))] rounded-sm text-[hsl(var(--muted-foreground))]">
                        ATTEMPT {String(attempt.n).padStart(2, "0")}
                    </div>
                    <StatusBadge status={isRejected ? "blocked" : "approved"} />
                    {forceOverstate && (
                        <span className="text-[10px] font-mono px-2 py-0.5 border border-[hsl(var(--warning))]/40 text-[hsl(var(--warning))] rounded-sm">
                            DEMO · overstate injected
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-3 text-[10px] font-mono text-[hsl(var(--muted-foreground))]">
                    <ModelBadge log={attempt.insight} role="insight" />
                    <span className="opacity-40">→</span>
                    <ModelBadge log={attempt.critic} role="critic" />
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-0 divide-y md:divide-y-0 md:divide-x divide-[hsl(var(--border))]">
                {/* LEFT — insight output ("claim") */}
                <div className="p-5">
                    <div className="label-caps text-[hsl(var(--muted-foreground))] mb-3 flex items-center gap-1.5">
                        <Wand2 size={11} /> Insight agent output
                    </div>
                    {prevAttempt && attempt.insight?.payload?.feedback_received && (
                        <div className="mb-3 text-xs font-mono text-[hsl(var(--muted-foreground))] border-l border-[hsl(var(--warning))]/40 pl-2">
                            reviewer feedback in prompt: “{(attempt.insight.payload.feedback_received || "").slice(0, 160)}…”
                        </div>
                    )}
                    <ol className="space-y-2">
                        {findings.length === 0 && <li className="text-xs text-[hsl(var(--muted-foreground))]">No findings parsed for this attempt.</li>}
                        {findings.slice(0, 4).map((f, i) => {
                            const isOverstate = forceOverstate && i === 0 && (f.title || "").toLowerCase().includes("overstate");
                            const isSuspect = isRejected && i === 0;
                            return (
                                <li key={i} className={`text-sm ${isOverstate || isSuspect ? "text-[hsl(var(--warning))]" : "text-white"}`}>
                                    <span className="font-mono text-[10px] text-[hsl(var(--muted-foreground))] mr-2">#{String(i + 1).padStart(2, "0")}</span>
                                    <span className={i === 0 ? "font-heading font-bold" : ""}>{f.title || f.claim}</span>
                                    {(isOverstate || isSuspect) && (
                                        <span className="ml-2 text-[10px] font-mono px-1.5 py-0.5 border border-[hsl(var(--warning))]/40 text-[hsl(var(--warning))] rounded-sm">SUSPECT</span>
                                    )}
                                </li>
                            );
                        })}
                    </ol>
                </div>

                {/* MIDDLE — critic verdict */}
                <div className="p-5 bg-[hsl(var(--background))]/40">
                    <div className="label-caps text-[hsl(var(--muted-foreground))] mb-3 flex items-center gap-1.5">
                        {isRejected ? <ShieldAlert size={11} className="text-[hsl(var(--warning))]" /> : <ShieldCheck size={11} className="text-[hsl(var(--success))]" />}
                        Critic decision
                    </div>
                    <div
                        className={`inline-block text-sm font-heading font-bold px-3 py-1 border rounded-sm mb-3 ${
                            isRejected
                                ? "text-[hsl(var(--warning))] border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10"
                                : "text-[hsl(var(--success))] border-[hsl(var(--success))]/40 bg-[hsl(var(--success))]/10"
                        }`}
                    >
                        {isRejected ? "REJECTED" : "APPROVED"}
                    </div>
                    <div className="text-sm leading-relaxed text-[hsl(var(--foreground))]">
                        {attempt.critic?.payload?.reasoning || attempt.decision?.reasoning || "—"}
                    </div>
                    {criticIssues.length > 0 && (
                        <ul className="mt-3 space-y-1.5" data-testid="critic-issues">
                            {criticIssues.map((iss, i) => (
                                <li key={i} className="text-xs font-mono text-[hsl(var(--muted-foreground))] border-l border-[hsl(var(--warning))]/40 pl-2">
                                    <span className="text-[hsl(var(--warning))]">[{iss.target || "target?"}]</span>{" "}
                                    {iss.problem || "—"}
                                    {iss.suggestion && <div className="opacity-70 mt-0.5">→ {iss.suggestion}</div>}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>

                {/* RIGHT — retry outcome / final action */}
                <div className="p-5">
                    <div className="label-caps text-[hsl(var(--muted-foreground))] mb-3 flex items-center gap-1.5">
                        {isRejected ? <RefreshCcw size={11} /> : <FileWarning size={11} />}
                        {isRejected ? "Retry queued" : "Outcome"}
                    </div>
                    {isRejected ? (
                        <div>
                            <div className="text-sm text-white leading-relaxed">
                                Feedback routed back to <span className="text-[hsl(var(--warning))] font-mono">insight_agent</span> for attempt {attempt.n + 1}.
                            </div>
                            <div className="mt-3 flex items-center gap-2 text-xs font-mono text-[hsl(var(--muted-foreground))]">
                                <ArrowRight size={11} /> See attempt {attempt.n + 1} below for the corrected claim.
                            </div>
                        </div>
                    ) : (
                        <div className="text-sm leading-relaxed">
                            <div className="text-white">Findings passed independent review and were persisted as the job's final output.</div>
                            {isFinal && (
                                <div className="mt-3 text-xs font-mono text-[hsl(var(--muted-foreground))]">
                                    total tokens attempt {attempt.n}: {
                                        ((attempt.insight?.payload?.usage?.total_tokens_estimated) || 0)
                                      + ((attempt.critic?.payload?.usage?.total_tokens_estimated) || 0)
                                    }
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Before/after diff — only rendered when this rejected attempt has a successor */}
            {isRejected && (
                <BeforeAfterStrip attempt={attempt} nextAttempt={nextAttempt} />
            )}
        </section>
    );
}

function ModelBadge({ log, role }) {
    if (!log) return <span className="opacity-40">{role} —</span>;
    const u = log.payload?.usage || {};
    return (
        <span>
            {role} · <span className="text-[hsl(var(--foreground))]">{u.provider}/{u.model}</span> · {u.total_tokens_estimated || 0}t · {u.elapsed_seconds || 0}s
        </span>
    );
}

function BeforeAfterStrip({ attempt, nextAttempt }) {
    const reasoning = attempt.critic?.payload?.reasoning || "";
    // The "before": the SUSPECT (first) finding of the rejected attempt.
    const rejected = (attempt.findings?.findings || [])[0];
    // The "after": the top finding produced on the successor attempt.
    const corrected = (nextAttempt?.findings?.findings || [])[0];
    return (
        <div className="border-t border-[hsl(var(--border))] bg-[hsl(var(--background))]/50 px-5 py-4" data-testid="before-after-strip">
            <div className="label-caps text-[hsl(var(--muted-foreground))] mb-2">
                Numeric contradiction cited by critic
            </div>
            <div className="font-mono text-xs leading-relaxed text-[hsl(var(--foreground))]">
                {reasoning}
            </div>
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="border border-[hsl(var(--warning))]/40 rounded-sm p-3 bg-[hsl(var(--warning))]/5">
                    <div className="label-caps text-[hsl(var(--warning))] mb-1.5">
                        Before · rejected claim
                    </div>
                    <div className="text-sm text-white font-heading font-bold leading-snug">
                        {rejected?.title || rejected?.claim || "—"}
                    </div>
                    {rejected?.claim && rejected?.title && rejected.claim !== rejected.title && (
                        <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1.5">
                            {rejected.claim}
                        </div>
                    )}
                    {rejected?.evidence && (
                        <div className="mt-2 font-mono text-[10px] text-[hsl(var(--warning))]">
                            evidence cited: {JSON.stringify(rejected.evidence)}
                        </div>
                    )}
                </div>
                <div className="border border-[hsl(var(--success))]/40 rounded-sm p-3 bg-[hsl(var(--success))]/5">
                    <div className="label-caps text-[hsl(var(--success))] mb-1.5">
                        After · corrected on next attempt
                    </div>
                    <div className="text-sm text-white font-heading font-bold leading-snug">
                        {corrected?.title || corrected?.claim || "— (no successor attempt)"}
                    </div>
                    {corrected?.claim && corrected?.title && corrected.claim !== corrected.title && (
                        <div className="text-xs text-[hsl(var(--muted-foreground))] mt-1.5">
                            {corrected.claim}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
