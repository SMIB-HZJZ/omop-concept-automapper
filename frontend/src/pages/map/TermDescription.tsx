import { FileText } from "lucide-react";
import { useState } from "react";

// Longer descriptions start collapsed so the search box stays in view
const COLLAPSE_AFTER = 220;

/** The description a source list gives a term (CSV column ``source_description``), e.g. when
 *  clinicians should use the code. The AI mapping steps see it too. */
export function TermDescription({ text }: { text: string }) {
  const long = text.length > COLLAPSE_AFTER;
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-muted/40 rounded-md border px-3 py-2 text-sm">
      <div className="text-muted-foreground mb-0.5 flex items-center gap-1.5 text-xs font-medium">
        <FileText className="size-3.5" aria-hidden />
        Description from the source list
        <span className="font-normal">· also given to the AI</span>
      </div>
      <p className="leading-relaxed whitespace-pre-line">
        {long && !open ? `${text.slice(0, COLLAPSE_AFTER).trimEnd()}…` : text}
      </p>
      {long && (
        <button
          type="button"
          className="text-primary mt-0.5 cursor-pointer text-xs hover:underline"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}
