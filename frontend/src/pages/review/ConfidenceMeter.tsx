/** Confidence 1-10 as a number and a coloured bar (green ≥ 8, amber ≥ 5, red below). */
export function ConfidenceMeter({ value }: { value: number }) {
  const tone = value >= 8 ? "bg-success" : value >= 5 ? "bg-warning" : "bg-destructive";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${value} of 10`}>
      <span className="w-5 text-sm font-semibold tabular-nums">{value}</span>
      <div className="bg-muted h-1.5 w-14 overflow-hidden rounded-full">
        <div className={`${tone} h-full rounded-full`} style={{ width: `${value * 10}%` }} />
      </div>
    </div>
  );
}
