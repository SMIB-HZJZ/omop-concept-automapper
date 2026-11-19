import { Pill, Stethoscope } from "lucide-react";
import { motion } from "motion/react";
import { useId } from "react";

import { cn } from "@/lib/utils";

const OPTIONS = [
  { value: false, label: "General", icon: Stethoscope },
  { value: true, label: "Drugs", icon: Pill },
] as const;

/** The one place where a source vocabulary is marked as drugs. Drug mode turns on ATC7
 * filtering and the structured drug match, for auto-mapping and manual mapping alike. */
export function VocabularyTypeSwitch({
  isDrug,
  onChange,
  disabled,
  label = "Vocabulary type",
}: {
  isDrug: boolean;
  onChange: (isDrug: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <span id={id} className="text-sm leading-none font-medium">
        {label}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={id}
        className="bg-muted inline-flex h-9 w-fit items-center gap-0.5 rounded-lg p-0.5"
      >
        {OPTIONS.map(({ value, label: text, icon: Icon }) => {
          const active = isDrug === value;
          return (
            <button
              key={text}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => !active && onChange(value)}
              className={cn(
                "relative flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                active ? "text-foreground font-medium" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {active && (
                <motion.span
                  layoutId={`${id}-thumb`}
                  className="bg-background absolute inset-0 rounded-md shadow-sm"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <Icon className="relative size-4" />
              <span className="relative">{text}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Read-only note on which mode a mapping run uses, with where to change it. */
export function DrugModeNote({ isDrug }: { isDrug: boolean }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg px-3 py-2 text-xs leading-relaxed",
        isDrug ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground",
      )}
    >
      {isDrug ? <Pill className="mt-0.5 size-3.5 shrink-0" /> : <Stethoscope className="mt-0.5 size-3.5 shrink-0" />}
      <span>
        {isDrug ? (
          <>
            <b>Drug mode</b>: ATC7 codes at the start of source values (e.g. A10BA02) narrow the search, and drugs are
            matched on ingredient, strength and dose form.
          </>
        ) : (
          <>
            <b>General mode</b>: no drug-specific rules.
          </>
        )}{" "}
        Change it with <i>Vocabulary type</i> above the table.
      </span>
    </div>
  );
}
