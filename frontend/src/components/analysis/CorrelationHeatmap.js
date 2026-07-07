import { useEffect, useState } from "react";

// Renders the Pearson correlation matrix from Phase-2 stat_result.
// Colors: reuse info blue (#3B82F6) for positive, critical red (#EF4444)
// for negative, both from design_guidelines.status. No new palette.
export default function CorrelationHeatmap({ data }) {
    const [hovered, setHovered] = useState(null);
    const [progress, setProgress] = useState(0);

    useEffect(() => {
        setProgress(0);
        const t = setTimeout(() => setProgress(1), 40);
        return () => clearTimeout(t);
    }, [data]);

    if (!data || !Array.isArray(data.matrix) || !Array.isArray(data.columns)) {
        return <div className="font-mono text-xs text-[hsl(var(--muted-foreground))] py-6">No correlation matrix available.</div>;
    }
    const { columns, matrix } = data;
    const n = columns.length;
    // Compute an SVG grid.
    const cell = 60;
    const gap = 4;
    const margin = 46;
    const width = margin + n * (cell + gap);
    const height = margin + n * (cell + gap);

    return (
        <div className="relative" data-testid="correlation-heatmap">
            <svg width={width} height={height} className="overflow-visible">
                {/* column labels (top) */}
                {columns.map((c, i) => (
                    <text
                        key={`col-${i}`}
                        x={margin + i * (cell + gap) + cell / 2}
                        y={margin - 12}
                        textAnchor="middle"
                        className="fill-[hsl(var(--muted-foreground))]"
                        style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }}
                    >
                        {c}
                    </text>
                ))}
                {/* row labels (left) */}
                {columns.map((c, i) => (
                    <text
                        key={`row-${i}`}
                        x={margin - 10}
                        y={margin + i * (cell + gap) + cell / 2 + 4}
                        textAnchor="end"
                        className="fill-[hsl(var(--muted-foreground))]"
                        style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11 }}
                    >
                        {c}
                    </text>
                ))}
                {matrix.map((row, i) =>
                    row.map((v, j) => {
                        const abs = Math.abs(v);
                        const intensity = Math.min(1, abs);
                        // positive => blue (info 217), negative => red (0)
                        const hue = v >= 0 ? 217 : 0;
                        const bg = `hsl(${hue}, 91%, 60%, ${(intensity * progress).toFixed(3)})`;
                        const showText = abs >= 0.02;
                        const isHover = hovered && hovered.i === i && hovered.j === j;
                        return (
                            <g key={`${i}-${j}`}>
                                <rect
                                    x={margin + j * (cell + gap)}
                                    y={margin + i * (cell + gap)}
                                    width={cell}
                                    height={cell}
                                    fill={bg}
                                    stroke={isHover ? "hsl(var(--primary))" : "hsl(var(--border))"}
                                    strokeWidth={isHover ? 1.5 : 1}
                                    onMouseEnter={() => setHovered({ i, j })}
                                    onMouseLeave={() => setHovered(null)}
                                    style={{ transition: "fill 400ms ease-out, stroke 120ms" }}
                                    data-testid={`heatmap-cell-${i}-${j}`}
                                />
                                {showText && (
                                    <text
                                        x={margin + j * (cell + gap) + cell / 2}
                                        y={margin + i * (cell + gap) + cell / 2 + 4}
                                        textAnchor="middle"
                                        style={{
                                            fontFamily: "JetBrains Mono, monospace",
                                            fontSize: 11,
                                            fill: abs > 0.5 ? "white" : "hsl(var(--foreground))",
                                            opacity: progress,
                                            transition: "opacity 500ms ease-out",
                                            pointerEvents: "none",
                                        }}
                                    >
                                        {v.toFixed(2)}
                                    </text>
                                )}
                            </g>
                        );
                    }),
                )}
            </svg>
            {hovered && (
                <div
                    className="absolute top-1 right-1 border border-[hsl(var(--border))] bg-[hsl(var(--background))] rounded-sm px-3 py-2 font-mono text-xs"
                    data-testid="heatmap-tooltip"
                >
                    <div className="text-[hsl(var(--muted-foreground))]">
                        {columns[hovered.i]} × {columns[hovered.j]}
                    </div>
                    <div className="text-white text-sm mt-0.5">
                        r = {matrix[hovered.i][hovered.j].toFixed(4)}
                    </div>
                </div>
            )}
            <div className="mt-3 flex items-center gap-4 text-[10px] font-mono text-[hsl(var(--muted-foreground))]">
                <span>−1</span>
                <div
                    className="h-1.5 flex-1 rounded-sm"
                    style={{
                        background:
                            "linear-gradient(to right, hsl(0,91%,60%,0.8), hsl(0,91%,60%,0), hsl(217,91%,60%,0), hsl(217,91%,60%,0.8))",
                    }}
                />
                <span>+1</span>
            </div>
        </div>
    );
}
