const PALETTE = {
    ready: { hue: 158, label: "READY", fg: "hsl(158,79%,55%)" },
    scan_clean: { hue: 158, label: "CLEAN", fg: "hsl(158,79%,55%)" },
    uploaded: { hue: 217, label: "UPLOADED", fg: "hsl(217,91%,60%)" },
    scanning: { hue: 217, label: "SCANNING", fg: "hsl(217,91%,60%)" },
    profiling: { hue: 38, label: "PROFILING", fg: "hsl(38,92%,55%)" },
    failed: { hue: 0, label: "FAILED", fg: "hsl(0,84%,62%)" },
    clean: { hue: 158, label: "CLEAN", fg: "hsl(158,79%,55%)" },
    approved: { hue: 158, label: "APPROVED", fg: "hsl(158,79%,55%)" },
    blocked: { hue: 0, label: "BLOCKED", fg: "hsl(0,84%,62%)" },
    flagged: { hue: 38, label: "FLAGGED", fg: "hsl(38,92%,55%)" },
    pending: { hue: 217, label: "PENDING", fg: "hsl(217,91%,60%)" },
};

export default function StatusBadge({ status, testId }) {
    if (!status) return null;
    const key = String(status).toLowerCase();
    const p = PALETTE[key] || { hue: 240, label: key.toUpperCase(), fg: "hsl(240,5%,70%)" };
    return (
        <span
            className="badge"
            data-testid={testId || `status-badge-${key}`}
            style={{
                color: p.fg,
                background: `hsl(${p.hue} 79% 40% / 0.10)`,
                borderColor: `hsl(${p.hue} 79% 40% / 0.35)`,
            }}
        >
            <span className="badge-dot" />
            {p.label}
        </span>
    );
}
