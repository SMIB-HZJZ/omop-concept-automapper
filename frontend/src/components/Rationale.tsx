import { ShieldCheck, Users } from "lucide-react";

import { cn } from "@/lib/utils";

// Tags the reranker puts at the start of a rationale (see backend workflows/nodes/rerank.py)
const TAGS: Record<string, { label: string; icon: typeof ShieldCheck; title: string }> = {
  "unsure first answer, decided by the stronger model": {
    label: "checked by a stronger model",
    icon: ShieldCheck,
    title: "The first model was unsure, so a stronger model decided",
  },
  "voters disagreed, judge decided": {
    label: "models disagreed",
    icon: Users,
    title: "The cheaper models disagreed, so the judge model decided",
  },
  "voters agreed": { label: "models agreed", icon: Users, title: "Two models picked the same concept" },
};

/** The AI's reason, with the internal "[route]" tag shown as a small chip instead of text. */
export function Rationale({
  text,
  className,
  clamp,
}: {
  text: string | null | undefined;
  className?: string;
  clamp?: boolean;
}) {
  if (!text) return null;
  const m = text.match(/^\[([^\]]+)\]\s*/);
  const tag = m ? TAGS[m[1]] : undefined;
  const body = m && tag ? text.slice(m[0].length) : text;
  return (
    <span className={cn("block", className)}>
      {tag && (
        <span
          title={tag.title}
          className="bg-accent text-accent-foreground mr-1.5 inline-flex items-center gap-1 rounded-full px-1.5 py-px align-middle text-[11px] font-medium"
        >
          <tag.icon className="size-3" />
          {tag.label}
        </span>
      )}
      <span className={cn(clamp && "line-clamp-2")} title={clamp ? body : undefined}>
        {body}
      </span>
    </span>
  );
}
