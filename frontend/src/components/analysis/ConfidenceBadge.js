// High = success hue from design_guidelines.status.success (#10B981)
// Low  = warning hue (#F59E0B), no new colors introduced.
export default function ConfidenceBadge({ confidence }) {
    if (!confidence) return null;
    const isHigh = confidence === "high";
    const fg = isHigh ? "hsl(158,79%,55%)" : "hsl(38,92%,55%)";
    const hue = isHigh ? 158 : 38;
    const label = isHigh ? "CRITIC APPROVED · HIGH CONFIDENCE" : "LOW CONFIDENCE · CRITIC OBJECTIONS RETAINED";
    return (
        <span
            data-testid="confidence-badge"
            className="badge"
            style={{
                color: fg,
                background: `hsl(${hue} 79% 40% / 0.10)`,
                borderColor: `hsl(${hue} 79% 40% / 0.35)`,
            }}
        >
            <span className="badge-dot" />
            {label}
        </span>
    );
}
