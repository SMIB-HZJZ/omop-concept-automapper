import { cn } from "@/lib/utils";

/** The institute that runs the app and the community whose vocabularies it maps to. The logos
 * have dark text, so they sit on a light panel in both themes. */
export function Logos({ className, large }: { className?: string; large?: boolean }) {
  const img = large ? "h-9 w-auto" : "h-6 w-auto";
  return (
    <div
      className={cn(
        "flex items-center justify-center gap-4 rounded-xl bg-white px-3 py-2.5 ring-1 ring-black/5",
        className,
      )}
    >
      <a href="https://www.hzjz.hr/" target="_blank" rel="noreferrer" title="Hrvatski zavod za javno zdravstvo">
        <img src="/logos/hzjz.png" alt="Hrvatski zavod za javno zdravstvo" className={img} />
      </a>
      <a
        href="https://www.ohdsi.org/"
        target="_blank"
        rel="noreferrer"
        title="OHDSI: Observational Health Data Sciences and Informatics"
      >
        <img src="/logos/ohdsi.png" alt="OHDSI" className={img} />
      </a>
    </div>
  );
}
