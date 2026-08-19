interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  positive?: boolean;
}

export default function Sparkline({
  data,
  width = 80,
  height = 28,
  positive,
}: SparklineProps) {
  if (data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 1;

  const coords = data.map((v, i) => ({
    x: pad + (i / (data.length - 1)) * (width - pad * 2),
    y: pad + (1 - (v - min) / range) * (height - pad * 2),
  }));

  const isPos = positive ?? data[data.length - 1] >= data[0];
  const color = isPos ? "#10b981" : "#f43f5e";
  const uid = Math.random().toString(36).slice(2, 8);

  const linePath = coords
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");

  const areaPath =
    linePath +
    ` L ${coords[coords.length - 1].x.toFixed(1)},${height} L ${coords[0].x.toFixed(1)},${height} Z`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id={`sg-${uid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#sg-${uid})`} />
      <path
        d={linePath}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle
        cx={coords[coords.length - 1].x.toFixed(1)}
        cy={coords[coords.length - 1].y.toFixed(1)}
        r="2"
        fill={color}
      />
    </svg>
  );
}
