import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { DESCRIPTION_HINT, DESCRIPTION_PLACEHOLDER, VOCABULARY_DESCRIPTION_MAX } from "@/lib/vocabularyText";
import { cn } from "@/lib/utils";

/** Optional description of a source vocabulary, with a character count. */
export function VocabularyDescriptionField({
  id,
  value,
  onChange,
  className,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  const left = VOCABULARY_DESCRIPTION_MAX - value.length;
  return (
    <div className={cn("grid gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={id}>
          Description <span className="text-muted-foreground font-normal">(optional, recommended)</span>
        </Label>
        <span className={cn("text-xs tabular-nums", left < 50 ? "text-warning" : "text-muted-foreground")}>{left}</span>
      </div>
      <Textarea
        id={id}
        rows={2}
        value={value}
        maxLength={VOCABULARY_DESCRIPTION_MAX}
        placeholder={DESCRIPTION_PLACEHOLDER}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-muted-foreground text-xs">{DESCRIPTION_HINT}</p>
    </div>
  );
}
