import { cn } from "@/lib/utils";

const COLORS = [
  "bg-sky-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-violet-500",
  "bg-teal-500",
  "bg-orange-500",
  "bg-fuchsia-500",
];

/** A coloured circle with a user's initials (colour stable per name). */
export function UserAvatar({ name, className, title }: { name: string; className?: string; title?: string }) {
  const hash = [...name].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
  const initials = name
    .split(/[\s._@-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
  return (
    <span
      title={title ?? name}
      className={cn(
        "ring-background inline-flex size-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold text-white ring-2",
        COLORS[hash % COLORS.length],
        className,
      )}
    >
      {initials || "?"}
    </span>
  );
}
