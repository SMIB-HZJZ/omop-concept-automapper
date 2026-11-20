import { useConceptFacets } from "@/api/queries";
import { MultiSelect } from "@/components/MultiSelect";
import { Label } from "@/components/ui/label";
import { type ConceptFilterValues, facetOptions } from "@/lib/conceptFacets";

const FIELDS: { key: keyof ConceptFilterValues; label: string; all: string }[] = [
  { key: "domains", label: "Domain", all: "All domains" },
  { key: "vocabularies", label: "Vocabulary", all: "All vocabularies" },
  { key: "conceptClasses", label: "Concept class", all: "All concept classes" },
];

/** Athena's filters for standard concepts: each list shows only what the other choices leave. */
export function ConceptFilterFields({
  idPrefix,
  values,
  onChange,
  only,
}: {
  idPrefix: string;
  values: ConceptFilterValues;
  onChange: (values: ConceptFilterValues) => void;
  /** Show only some of the filters (the rest are placed elsewhere) */
  only?: (keyof ConceptFilterValues)[];
}) {
  const facets = useConceptFacets().data ?? [];
  return (
    <>
      {FIELDS.filter((f) => !only || only.includes(f.key)).map((f) => (
        <div key={f.key} className="grid gap-1.5">
          <Label htmlFor={`${idPrefix}-${f.key}`}>{f.label}</Label>
          <MultiSelect
            id={`${idPrefix}-${f.key}`}
            options={facetOptions(facets, f.key, values)}
            value={values[f.key]}
            onChange={(next) => onChange({ ...values, [f.key]: next })}
            placeholder={f.all}
          />
        </div>
      ))}
    </>
  );
}
