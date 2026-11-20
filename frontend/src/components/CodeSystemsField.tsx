import { Label } from "@/components/ui/label";
import { MultiSelect } from "@/components/MultiSelect";

/** OMOP vocabularies whose codes can appear in source_value (MKB-10 = ICD10). */
const CODE_VOCABULARIES = [
  "ICD10",
  "ICD10CM",
  "ICD9CM",
  "ICD10PCS",
  "LOINC",
  "SNOMED",
  "ATC",
  "NDC",
  "CPT4",
  "Read",
];

/** Which code system the source_value codes use. Auto-mapping and AI suggest look a code up there
 *  first and follow "Maps to": an exact mapping without the AI. */
export function CodeSystemsField({
  id,
  value,
  onChange,
  className,
}: {
  id: string;
  value: string[];
  onChange: (value: string[]) => void;
  className?: string;
}) {
  return (
    <div className={className ? `grid gap-1.5 ${className}` : "grid gap-1.5"}>
      <Label htmlFor={id}>Source codes are</Label>
      <MultiSelect
        id={id}
        options={CODE_VOCABULARIES}
        value={value}
        onChange={onChange}
        placeholder="Not a code system"
      />
      <p className="text-muted-foreground text-xs">
        When <code>source_value</code> holds codes of a standard system (e.g. ICD10 for MKB-10), each code is looked up
        there and mapped through "Maps to" before the AI is asked. Leave empty for local codes.
      </p>
    </div>
  );
}
