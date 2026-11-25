import { Check, Loader2, Pencil, Pill, Plus, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import type { DrugMatch, DrugStructure } from "@/api/types";
import { ErrorAlert } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { checkForm, emptyRow, formatStrength, fromForm, toForm, type StructureForm } from "@/lib/drugStructure";
import { cn } from "@/lib/utils";

const STATUS: Record<DrugMatch["status"], { label: string; className: string; hint: string }> = {
  exact: {
    label: "Exact match",
    className: "border-success/40 bg-success/10 text-success",
    hint: "One drug has these ingredients, strengths and form; auto-mapping accepts it without the AI",
  },
  tie: {
    label: "Several matches",
    className: "border-primary/30 bg-primary/10 text-primary",
    hint: "Several drugs fit equally well; they are listed first below",
  },
  approximate: {
    label: "Close match",
    className: "border-warning/40 bg-warning/10 text-warning",
    hint: "No exact drug; the closest ones are listed first below",
  },
  none: {
    label: "No matching drug",
    className: "text-muted-foreground",
    hint: "No drug with this structure; the search below uses it anyway",
  },
  not_drug: {
    label: "Not a drug",
    className: "text-muted-foreground",
    hint: "The AI read the name as something other than a medicinal product",
  },
  parse_error: { label: "Could not read", className: "text-destructive", hint: "Reading the name failed" },
  no_structure: { label: "No structure", className: "text-muted-foreground", hint: "Nothing to match" },
  unavailable: {
    label: "Unavailable",
    className: "text-muted-foreground",
    hint: "Drug matching needs the DRUG_STRENGTH table of the vocabulary",
  },
};

const ORIGIN: Record<NonNullable<DrugMatch["origin"]>, string> = {
  provided: "from the source file",
  parsed: "read from the name by AI",
  edited: "edited",
};

const LEVELS: Record<string, string> = {
  branded_drug: "Branded Drug",
  quant_branded_drug: "Quant Branded Drug",
  branded_drug_comp: "Branded Drug Comp",
  branded_drug_form: "Branded Drug Form",
  clinical_drug: "Clinical Drug",
  quant_clinical_drug: "Quant Clinical Drug",
  clinical_drug_comp: "Clinical Drug Comp",
  clinical_drug_form: "Clinical Drug Form",
  ingredient: "Ingredient",
};

function Chip({ label, children, muted }: { label: string; children: React.ReactNode; muted?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-baseline gap-1 rounded-md border px-2 py-0.5 text-xs",
        muted ? "text-muted-foreground border-dashed" : "bg-background",
      )}
    >
      <span className="text-muted-foreground text-[10px] tracking-wide uppercase">{label}</span>
      {children}
    </span>
  );
}

function StructureChips({ structure }: { structure: DrugStructure }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {structure.components?.map((c, i) => (
        <Chip key={i} label="Ingredient">
          <b className="font-medium">{c.ingredient}</b>
          {formatStrength(c) && <span className="tabular-nums">{formatStrength(c)}</span>}
        </Chip>
      ))}
      <Chip label="Form" muted={!structure.dose_form}>
        {structure.dose_form ?? "—"}
      </Chip>
      <Chip label="Brand" muted={!structure.brand_name}>
        {structure.brand_name ?? "none"}
      </Chip>
      {structure.quantity_value != null && (
        <Chip label="Quantity">
          <span className="tabular-nums">
            {structure.quantity_value} {structure.quantity_unit}
          </span>
        </Chip>
      )}
    </div>
  );
}

function StructureEditor({
  initial,
  onMatch,
  onCancel,
}: {
  initial: StructureForm;
  onMatch: (s: DrugStructure) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState(initial);
  const errors = checkForm(form);
  const structure = errors.length ? null : fromForm(form);
  const setRow = (i: number, key: keyof StructureForm["rows"][number], value: string) =>
    setForm((f) => ({ ...f, rows: f.rows.map((r, j) => (j === i ? { ...r, [key]: value } : r)) }));
  const field = "h-8 text-sm";
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (structure) onMatch(structure);
      }}
    >
      <div className="text-muted-foreground grid grid-cols-[minmax(0,2fr)_4rem_4rem_4rem_4rem_2rem] gap-1.5 text-[10px] tracking-wide uppercase">
        <span>Ingredient</span>
        <span>Strength</span>
        <span>Unit</span>
        <span>Per</span>
        <span>Unit</span>
        <span />
      </div>
      {form.rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[minmax(0,2fr)_4rem_4rem_4rem_4rem_2rem] gap-1.5">
          <Input
            aria-label="Ingredient"
            className={field}
            value={r.ingredient}
            onChange={(e) => setRow(i, "ingredient", e.target.value)}
            placeholder="any language"
          />
          <Input
            aria-label="Strength"
            className={field}
            value={r.value}
            onChange={(e) => setRow(i, "value", e.target.value)}
          />
          <Input
            aria-label="Strength unit"
            className={field}
            value={r.unit}
            onChange={(e) => setRow(i, "unit", e.target.value)}
            placeholder="mg"
          />
          <Input
            aria-label="Per amount"
            className={field}
            value={r.perValue}
            onChange={(e) => setRow(i, "perValue", e.target.value)}
          />
          <Input
            aria-label="Per unit"
            className={field}
            value={r.perUnit}
            onChange={(e) => setRow(i, "perUnit", e.target.value)}
            placeholder="mL"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Remove ingredient"
            disabled={form.rows.length === 1}
            onClick={() => setForm((f) => ({ ...f, rows: f.rows.filter((_, j) => j !== i) }))}
          >
            <X />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="gap-1"
        onClick={() => setForm((f) => ({ ...f, rows: [...f.rows, emptyRow()] }))}
      >
        <Plus className="size-3.5" /> Ingredient
      </Button>
      <div className="grid gap-1.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_4rem_4rem]">
        <div className="grid gap-1">
          <Label htmlFor="drug-form" className="text-xs">
            Dose form
          </Label>
          <Input
            id="drug-form"
            className={field}
            value={form.doseForm}
            onChange={(e) => setForm({ ...form, doseForm: e.target.value })}
            placeholder="Oral Tablet, or in any language"
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="drug-brand" className="text-xs">
            Brand
          </Label>
          <Input
            id="drug-brand"
            className={field}
            value={form.brand}
            onChange={(e) => setForm({ ...form, brand: e.target.value })}
            placeholder="none"
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="drug-qty" className="text-xs" title="Total volume or mass of one unit, e.g. a 2 mL ampoule">
            Quantity
          </Label>
          <Input
            id="drug-qty"
            className={field}
            value={form.quantityValue}
            onChange={(e) => setForm({ ...form, quantityValue: e.target.value })}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="drug-qty-unit" className="text-xs">
            Unit
          </Label>
          <Input
            id="drug-qty-unit"
            className={field}
            value={form.quantityUnit}
            onChange={(e) => setForm({ ...form, quantityUnit: e.target.value })}
            placeholder="mL"
          />
        </div>
      </div>
      {errors.length > 0 && (
        <ul aria-live="polite" className="text-destructive list-disc space-y-0.5 pl-4 text-xs">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!structure}>
          Match
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

type Props = {
  match: DrugMatch | undefined;
  loading: boolean;
  error: unknown;
  /** The structure typed in the editor (null: the term's own) */
  edited: DrugStructure | null;
  onEdit: (structure: DrugStructure | null) => void;
  showBranded: boolean;
  onShowBranded: (show: boolean) => void;
  /** Tick the matched drugs */
  onUse: () => void;
  used: boolean;
  /** The user typed their own search text: it is searched as typed, without the drug filters */
  searchEdited: boolean;
};

/** The term's drug structure, how it was read and what it matches, as auto-mapping sees it. */
export function DrugPanel({
  match,
  loading,
  error,
  edited,
  onEdit,
  showBranded,
  onShowBranded,
  onUse,
  used,
  searchEdited,
}: Props) {
  const [editing, setEditing] = useState(false);
  const status = match ? STATUS[match.status] : null;
  const hidesBranded = (match?.exclude_classes?.length ?? 0) > 0;

  return (
    <section aria-label="Drug structure" className="bg-muted/30 space-y-2 rounded-xl border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Pill className="text-primary size-4" />
        <span className="text-sm font-medium">Drug structure</span>
        {match?.origin && <span className="text-muted-foreground text-xs">{ORIGIN[match.origin]}</span>}
        {loading ? (
          <span className="text-muted-foreground flex items-center gap-1 text-xs">
            <Loader2 className="size-3 animate-spin" />
            {match ? "Matching…" : "Reading the drug…"}
          </span>
        ) : (
          status && (
            <Badge variant="outline" className={status.className} title={status.hint}>
              {status.label}
              {match?.level && match.status !== "none" && match.status !== "tie"
                ? ` · ${LEVELS[match.level] ?? match.level}`
                : ""}
            </Badge>
          )
        )}
        <span className="ml-auto flex items-center gap-1">
          {match?.status === "exact" && match.candidates.length > 0 && (
            <Button
              size="sm"
              variant={used ? "secondary" : "default"}
              className="h-7 gap-1"
              onClick={onUse}
              disabled={used}
            >
              <Check className="size-3.5" /> {used ? "Ticked" : "Tick this drug"}
            </Button>
          )}
          {edited && !editing && (
            <Button size="sm" variant="ghost" className="h-7 gap-1" onClick={() => onEdit(null)}>
              <RotateCcw className="size-3.5" /> Original
            </Button>
          )}
          {!editing && (
            <Button size="sm" variant="ghost" className="h-7 gap-1" onClick={() => setEditing(true)} disabled={loading}>
              <Pencil className="size-3.5" /> Edit
            </Button>
          )}
        </span>
      </div>

      <ErrorAlert error={error} title="Drug matching failed" />

      {editing ? (
        <StructureEditor
          initial={toForm(match?.structure)}
          onCancel={() => setEditing(false)}
          onMatch={(s) => {
            setEditing(false);
            onEdit(s);
          }}
        />
      ) : (
        match?.structure && <StructureChips structure={match.structure} />
      )}

      {!editing && match && (
        <div className="space-y-1 text-xs">
          {match.resolved.length > 0 && (
            <p className="text-muted-foreground">
              <span className="font-medium">Read as:</span> {match.resolved.join("; ")}
            </p>
          )}
          {match.approximate.length > 0 && (
            <p className="text-warning">
              <span className="font-medium">Not exact:</span> {match.approximate.join("; ")}
            </p>
          )}
          {(match.status === "none" || match.status === "tie") && match.reason && (
            <p className="text-muted-foreground">{match.reason}</p>
          )}
          {match.status === "not_drug" && (
            <p className="text-muted-foreground">
              The AI does not read this term as a drug product. Search other domains, or Edit to give its structure.
            </p>
          )}
          {searchEdited ? (
            <p className="text-muted-foreground">
              Your search text is searched as typed: the structure query, its matches and the brand filter apply to the
              source term only.
            </p>
          ) : (
            match.search_text && (
              <p className="text-muted-foreground">
                <span className="font-medium">Also searching:</span>{" "}
                <span className="font-mono">{match.search_text}</span>
              </p>
            )
          )}
          {match.brand_in_name && (
            <p className="text-muted-foreground">
              The name contains the brand <b>{match.brand_in_name}</b>, so branded drugs stay in the results.
            </p>
          )}
          {hidesBranded && !searchEdited && (
            <div className="flex items-center gap-2 pt-0.5">
              <Switch id="show-branded" size="sm" checked={showBranded} onCheckedChange={onShowBranded} />
              <Label htmlFor="show-branded" className="text-xs font-normal">
                Show branded drugs{" "}
                <span className="text-muted-foreground">
                  (hidden: the term names no brand, and branded drugs would crowd out the generic ones)
                </span>
              </Label>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
