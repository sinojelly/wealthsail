interface SparklineProps {
  values: number[]
  positive: boolean
}

export default function Sparkline({ values, positive }: SparklineProps) {
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const points = values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 92 + 4
      const y = 25 - ((value - min) / range) * 19
      return `${x},${y}`
    })
    .join(" ")

  return (
    <svg
      aria-label={positive ? "近期趋势向上" : "近期趋势向下"}
      className="h-8 w-24"
      viewBox="0 0 100 32"
      role="img"
    >
      <polyline
        fill="none"
        points={points}
        stroke={positive ? "#22c55e" : "#fb7185"}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  )
}
