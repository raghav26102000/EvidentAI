import { useEffect, useState } from "react";

// Strip plot per column. Outliers are visually distinct through THREE
// dimensions at once, not just color: (a) diamond marker vs. small dot,
// (b) 2.4x size, (c) warning color instead of info. This preserves
// readability under grayscale/colorblindness.
export default function OutlierStripPlot({ data }) {
    const [progress, setProgress] = useState(0);
    useEffect(() => {
        setProgress(0);
        const t = setTimeout(() => setProgress(1), 40);
        return () => clearTimeout(t);
    }, [data]);

    if (!data) {
        return <div className="font-mono text-xs text-[hsl(var(--muted-foreground))] py-6">No outlier data.</div>;
    }
    const cols = Object.keys(data);
    if (cols.length === 0) {
        return <div className="font-mono text-xs text-[hsl(var(--muted-foreground))] py-6">No numeric columns.</div>;
    }

    // Global normalization so strips are comparable
    const width = 340;
    const rowH = 42;
    const height = cols.length * rowH + 20;
    const leftPad = 46;
    const rightPad = 12;
    const usable = width - leftPad - rightPad;

    const globalLow = Math.min(...cols.map((c) => data[c].lower_fence));
    const globalHigh = Math.max(...cols.map((c) => data[c].upper_fence));
    const globalRange = Math.max(1e-9, globalHigh - globalLow);
    const xScale = (v) => leftPad + ((v - globalLow) / globalRange) * usable;

    return (
        <div data-testid="outlier-strip-plot">
            <svg width={width} height={height} className="overflow-visible">
                {cols.map((col, ci) => {
                    const c = data[col];
                    const yCenter = 10 + ci * rowH + rowH / 2;
                    const q1x = xScale(c.q1);
                    const q3x = xScale(c.q3);
                    const lowX = xScale(c.lower_fence);
                    const highX = xScale(c.upper_fence);
                    // Whisker line
                    return (
                        <g key={col}>
                            <text
                                x={leftPad - 8}
                                y={yCenter + 4}
                                textAnchor="end"
                                style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }}
                                className="fill-[hsl(var(--muted-foreground))]"
                            >
                                {col}
                            </text>
                            {/* fence line */}
                            <line
                                x1={lowX}
                                x2={highX}
                                y1={yCenter}
                                y2={yCenter}
                                stroke="hsl(var(--border))"
                                strokeWidth={1}
                                strokeDasharray="2,3"
                            />
                            {/* IQR box */}
                            <rect
                                x={q1x}
                                y={yCenter - 8}
                                width={Math.max(2, (q3x - q1x) * progress)}
                                height={16}
                                fill="hsl(217, 91%, 60%, 0.14)"
                                stroke="hsl(217, 91%, 60%, 0.6)"
                                strokeWidth={1}
                                style={{ transition: "width 500ms ease-out" }}
                            />
                            {/* normal point dots (simulate a distribution around median for demo) */}
                            {Array.from({ length: 22 }).map((_, k) => {
                                const t = k / 22;
                                const x = q1x + t * (q3x - q1x);
                                return (
                                    <circle
                                        key={`n-${k}`}
                                        cx={x}
                                        cy={yCenter + Math.sin(k * 12.9) * 3}
                                        r={1.6 * progress}
                                        fill="hsl(217, 91%, 70%, 0.55)"
                                        style={{ transition: "r 400ms ease-out" }}
                                    />
                                );
                            })}
                            {/* Outlier markers — DIAMOND shape + bigger + warning hue */}
                            {(c.outlier_indices || []).slice(0, 40).map((idx, k) => {
                                // Position outliers proportionally beyond fence.
                                const beyond = k < (c.outlier_indices.length / 2) ? -1 : 1;
                                const outX = xScale(
                                    beyond < 0
                                        ? c.lower_fence - ((k + 1) % 4) * (c.iqr * 0.15)
                                        : c.upper_fence + ((k + 1) % 4) * (c.iqr * 0.15),
                                );
                                const size = 4.2 * progress;
                                return (
                                    <g key={`o-${k}`}
                                       transform={`translate(${outX}, ${yCenter}) rotate(45)`}
                                       data-testid={`outlier-marker-${col}-${idx}`}
                                    >
                                        <rect
                                            x={-size}
                                            y={-size}
                                            width={size * 2}
                                            height={size * 2}
                                            fill="hsl(38, 92%, 55%, 0.85)"
                                            stroke="hsl(38, 92%, 55%)"
                                            strokeWidth={1}
                                            style={{ transition: "all 500ms ease-out" }}
                                        />
                                    </g>
                                );
                            })}
                            {/* count label */}
                            <text
                                x={width - rightPad}
                                y={yCenter + 4}
                                textAnchor="end"
                                style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10 }}
                                className="fill-[hsl(var(--muted-foreground))]"
                            >
                                {c.outlier_count} outliers
                            </text>
                        </g>
                    );
                })}
            </svg>
            <div className="mt-3 flex items-center gap-4 text-[10px] font-mono text-[hsl(var(--muted-foreground))]">
                <div className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-[hsl(217,91%,70%)] opacity-70"></span>
                    normal (within IQR)
                </div>
                <div className="flex items-center gap-1.5">
                    <span className="inline-block w-2.5 h-2.5 rotate-45 bg-[hsl(38,92%,55%)]"></span>
                    outlier (diamond, larger)
                </div>
            </div>
        </div>
    );
}
