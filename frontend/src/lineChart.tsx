interface Point {
  x: string;
  y: number;
}

interface LineChartProps {
  points: Point[];
  color?: string;
  height?: number;
  unit?: string;
}

const WIDTH = 640;
const PADDING = 24;

export function LineChart({ points, color = "var(--amber)", height = 140, unit = "" }: LineChartProps) {
  if (points.length === 0) {
    return <div className="empty-state">No data yet.</div>;
  }
  const ys = points.map((p) => p.y);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(...ys, minY + 1);
  const stepX = points.length > 1 ? (WIDTH - PADDING * 2) / (points.length - 1) : 0;
  const scaleY = (y: number) =>
    height - PADDING - ((y - minY) / (maxY - minY)) * (height - PADDING * 2);
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${PADDING + i * stepX} ${scaleY(p.y)}`)
    .join(" ");

  return (
    <svg
      className="line-chart"
      viewBox={`0 0 ${WIDTH} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Trend chart"
    >
      <path d={path} className="line-chart__path" style={{ stroke: color }} />
      <text x={PADDING} y={14} className="line-chart__label">
        {maxY.toFixed(1)} {unit}
      </text>
      <text x={PADDING} y={height - 6} className="line-chart__label">
        {points[0].x} → {points[points.length - 1].x}
      </text>
    </svg>
  );
}
