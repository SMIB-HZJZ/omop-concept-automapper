import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** ISO 639-1 codes offered in the picker; any other two-letter code can be set through the API. */
const LANGUAGES: [string, string][] = [
  ["hr", "Croatian"],
  ["en", "English"],
  ["sl", "Slovenian"],
  ["sr", "Serbian"],
  ["bs", "Bosnian"],
  ["de", "German"],
  ["it", "Italian"],
  ["hu", "Hungarian"],
  ["fr", "French"],
  ["es", "Spanish"],
  ["la", "Latin"],
];

const DETECT = "detect";

/** Language of the source terms, told to the translation step. Empty: detected per term. */
export function SourceLanguageField({
  id,
  value,
  onChange,
  className,
}: {
  id: string;
  value: string | null;
  onChange: (value: string | null) => void;
  className?: string;
}) {
  // A code saved through the API that the list does not offer is still shown
  const options = value && !LANGUAGES.some(([code]) => code === value) ? [...LANGUAGES, [value, value]] : LANGUAGES;
  return (
    <div className={className ? `grid gap-1.5 ${className}` : "grid gap-1.5"}>
      <Label htmlFor={id}>Source language</Label>
      <Select value={value ?? DETECT} onValueChange={(v) => onChange(v === DETECT ? null : v)}>
        <SelectTrigger id={id} className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={DETECT}>Detect per term</SelectItem>
          {options.map(([code, label]) => (
            <SelectItem key={code} value={code}>
              {label} <span className="text-muted-foreground">({code})</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="text-muted-foreground text-xs">
        Tells the AI translation which language the terms are in, so short or ambiguous terms are not misread. English
        skips translation. Leave on detect for mixed-language lists.
      </p>
    </div>
  );
}
