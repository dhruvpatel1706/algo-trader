"use client";

/**
 * Radial-arc gauge for coherence ratio = live_WR / backtest_WR.
 * Pure SVG; no chart library dep. Thresholds shaded behind the arc.
 */
export function CoherenceGauge({
  ratio,
  thresholdWarn = 0.8,
  thresholdHalt = 0.6,
  size = 160,
  label,
}: {
  ratio: number;
  thresholdWarn?: number;
  thresholdHalt?: number;
  size?: number;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(1.5, ratio));
  // Map [0..1.5] -> arc 0..270deg starting at -135deg
  const startAngle = -135;
  const sweep = 270;
  const angle = startAngle + (clamped / 1.5) * sweep;

  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.4;

  const arc = (from: number, to: number) => {
    const f = polar(cx, cy, r, from);
    const t = polar(cx, cy, r, to);
    const large = to - from > 180 ? 1 : 0;
    return `M ${f.x} ${f.y} A ${r} ${r} 0 ${large} 1 ${t.x} ${t.y}`;
  };

  const haltEnd = startAngle + (thresholdHalt / 1.5) * sweep;
  const warnEnd = startAngle + (thresholdWarn / 1.5) * sweep;
  const fullEnd = startAngle + sweep;

  const tip = polar(cx, cy, r, angle);

  const color =
    ratio < thresholdHalt
      ? "#ef4444"
      : ratio < thresholdWarn
        ? "#f59e0b"
        : "#22c55e";

  return (
    <div className="flex flex-col items-center">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <path
          d={arc(startAngle, haltEnd)}
          stroke="#ef4444"
          strokeOpacity={0.25}
          strokeWidth={10}
          fill="none"
          strokeLinecap="round"
        />
        <path
          d={arc(haltEnd, warnEnd)}
          stroke="#f59e0b"
          strokeOpacity={0.25}
          strokeWidth={10}
          fill="none"
        />
        <path
          d={arc(warnEnd, fullEnd)}
          stroke="#22c55e"
          strokeOpacity={0.25}
          strokeWidth={10}
          fill="none"
          strokeLinecap="round"
        />
        <path
          d={arc(startAngle, angle)}
          stroke={color}
          strokeWidth={10}
          fill="none"
          strokeLinecap="round"
        />
        <circle cx={tip.x} cy={tip.y} r={6} fill={color} />
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          fill="#e4e4e7"
          fontSize={size * 0.18}
          fontFamily="ui-monospace, monospace"
          fontWeight={700}
        >
          {ratio.toFixed(2)}
        </text>
        <text
          x={cx}
          y={cy + size * 0.13}
          textAnchor="middle"
          fill="#7c8290"
          fontSize={size * 0.07}
        >
          live / backtest
        </text>
      </svg>
      {label && <div className="mt-1 text-xs text-muted">{label}</div>}
    </div>
  );
}

function polar(cx: number, cy: number, r: number, deg: number) {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
