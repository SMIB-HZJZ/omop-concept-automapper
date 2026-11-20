import { Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import type { KeyboardEvent } from "react";

import type { Suggestion } from "@/api/types";
import { ConceptLink } from "@/components/ConceptLink";
import { Rationale } from "@/components/Rationale";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { formatDate } from "@/lib/format";
import { ConfidenceMeter } from "./ConfidenceMeter";
import { methodLabel } from "@/lib/methods";

export type DetailPosition = { id: number; index: number };

/** One suggestion in full (long source terms and reasons are cut in the table), with accept and
 * reject, and J/K or ←/→ to move through the page without closing. */
export function SuggestionDetail({
  items,
  position,
  onPositionChange,
  onClose,
  onDecide,
  busy,
  pending,
}: {
  items: Suggestion[];
  position: DetailPosition | null;
  onPositionChange: (position: DetailPosition) => void;
  onClose: (index: number) => void;
  onDecide: (kind: "accept" | "reject", id: number) => void;
  busy: boolean;
  pending: boolean;
}) {
  // A decided suggestion leaves the list; the one that took its place is shown
  const found = position ? items.findIndex((s) => s.id === position.id) : -1;
  const index = found >= 0 ? found : Math.min(position?.index ?? 0, items.length - 1);
  const s = position && index >= 0 ? items[index] : undefined;

  const go = (to: number) => {
    if (to >= 0 && to < items.length) onPositionChange({ id: items[to].id, index: to });
  };
  const decide = (kind: "accept" | "reject") => {
    if (!s || busy) return;
    onDecide(kind, s.id);
    // Move on right away; the decided one disappears when the list reloads
    const next = items[index + 1];
    onPositionChange(next ? { id: next.id, index } : { id: s.id, index });
  };
  // Capture phase: concept links inside stop their key events from bubbling
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key.toLowerCase();
    if (key === "j" || e.key === "ArrowDown" || e.key === "ArrowRight") go(index + 1);
    else if (key === "k" || e.key === "ArrowUp" || e.key === "ArrowLeft") go(index - 1);
    else if (pending && (key === "a" || key === "r")) decide(key === "a" ? "accept" : "reject");
    else return;
    e.preventDefault();
  };

  return (
    <Dialog open={!!s} onOpenChange={(open) => !open && onClose(Math.max(index, 0))}>
      {s && (
        <DialogContent className="sm:max-w-2xl" onKeyDownCapture={onKeyDown}>
          <DialogHeader>
            <DialogDescription>
              Suggestion {index + 1} of {items.length} on this page
            </DialogDescription>
            <DialogTitle className="text-lg leading-snug break-words whitespace-pre-wrap">
              {s.source_concept_name}
            </DialogTitle>
            <div className="text-muted-foreground text-xs">
              <span className="font-mono break-all">{s.source_value}</span>
              {s.freq != null && <> · freq {s.freq.toLocaleString()}</>}
            </div>
          </DialogHeader>

          <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
            <section className="rounded-xl border p-3">
              <div className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                Suggested concept{(s.extra_concepts ?? []).length > 0 ? "s (mapped together)" : ""}
              </div>
              <ConceptLink conceptId={s.concept_id} className="font-semibold">
                {s.concept_name}
              </ConceptLink>
              <div className="text-muted-foreground text-xs">
                {s.domain_id} · {s.vocabulary_id} · {s.concept_class_id} · {s.concept_code} · ID {s.concept_id}
              </div>
              {(s.extra_concepts ?? []).map((c) => (
                <div key={c.concept_id} className="mt-2">
                  +{" "}
                  <ConceptLink conceptId={c.concept_id} className="font-semibold">
                    {c.concept_name}
                  </ConceptLink>
                  <div className="text-muted-foreground text-xs">{c.concept_class_id}</div>
                </div>
              ))}
              <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                <ConfidenceMeter value={s.confidence} />
                {s.match_type && (
                  <span
                    className={
                      s.match_type === "exact" ? "text-muted-foreground" : "text-amber-700 dark:text-amber-400"
                    }
                  >
                    {s.match_type} match
                  </span>
                )}
              </div>
            </section>

            <section>
              <div className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">Why</div>
              {s.rationale ? (
                <Rationale text={s.rationale} className="text-sm leading-relaxed break-words" />
              ) : (
                <p className="text-muted-foreground text-sm">No reason given.</p>
              )}
            </section>

            <div className="text-muted-foreground text-xs">
              {methodLabel(s.method)}
              {s.pipeline ? ` · ${s.pipeline}` : ""} · suggested {formatDate(s.created_at)}
              {s.decided_at && (
                <>
                  {" "}
                  · {s.status} by {s.decided_by ?? "—"} {formatDate(s.decided_at)}
                </>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t pt-4">
            <Button variant="outline" size="sm" disabled={index <= 0} onClick={() => go(index - 1)}>
              <ChevronLeft /> Previous
            </Button>
            <Button variant="outline" size="sm" disabled={index >= items.length - 1} onClick={() => go(index + 1)}>
              Next <ChevronRight />
            </Button>
            {pending && (
              <div className="ml-auto flex gap-2">
                <Button variant="outline" disabled={busy} onClick={() => decide("reject")} title="Reject (R)">
                  <X /> Reject
                </Button>
                <Button variant="cta" disabled={busy} onClick={() => decide("accept")} title="Accept (A)">
                  <Check /> Accept
                </Button>
              </div>
            )}
          </div>
          <p className="text-muted-foreground -mt-2 text-[11px]">
            Keyboard: <b>J</b>/<b>K</b> or arrows move{pending ? " · A accept · R reject" : ""} · Esc close
          </p>
        </DialogContent>
      )}
    </Dialog>
  );
}
