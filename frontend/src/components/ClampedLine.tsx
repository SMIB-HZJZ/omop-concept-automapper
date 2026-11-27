import type { LucideIcon } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A labelled text kept to one line, with "More" when it is cut off; for context that is useful
 *  but must not push the work area down (e.g. a term's or a vocabulary's description). */
export function ClampedLine({
  icon: Icon,
  label,
  text,
  what,
  className,
}: {
  icon: LucideIcon;
  label: ReactNode;
  text: string;
  /** For the button's accessible name, e.g. "description" */
  what: string;
  className?: string;
}) {
  const ref = useRef<HTMLParagraphElement>(null);
  const id = useId();
  const [open, setOpen] = useState(false);
  const [clipped, setClipped] = useState(false);

  // Offer "More" only when the line is actually cut off (depends on the width)
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const measure = () => setClipped(el.scrollWidth > el.clientWidth + 1);
    measure();
    // Web fonts arriving later can change the text width without resizing the element
    void document.fonts?.ready.then(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, open]);

  return (
    <div className={cn("text-muted-foreground flex min-w-0 flex-1 items-start gap-1.5 text-sm", className)}>
      <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <p
        ref={ref}
        id={id}
        className={cn("min-w-0 flex-1 leading-snug", open ? "max-h-32 overflow-y-auto" : "truncate")}
      >
        <span className="text-foreground font-medium">{label} </span>
        {text}
      </p>
      {(clipped || open) && (
        <button
          type="button"
          className="text-primary shrink-0 cursor-pointer text-xs leading-5 hover:underline"
          aria-expanded={open}
          aria-controls={id}
          aria-label={open ? `Show less of the ${what}` : `Show the full ${what}`}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Less" : "More"}
        </button>
      )}
    </div>
  );
}
