import type { DrugComponent, DrugStructure } from "@/api/types";

const num = (v: number | null | undefined) => (v == null ? "" : String(Number(v.toPrecision(6))));

/** "500 mg", "10 mg / 2 mL", "5 mg/mL" or "" for a component without a strength. */
export function formatStrength(c: DrugComponent): string {
  if (c.amount_value != null) return `${num(c.amount_value)} ${c.amount_unit ?? ""}`.trim();
  if (c.numerator_value != null) {
    const top = `${num(c.numerator_value)} ${c.numerator_unit ?? ""}`.trim();
    if (c.denominator_value != null && c.denominator_value !== 1)
      return `${top} / ${num(c.denominator_value)} ${c.denominator_unit ?? ""}`.trim();
    return c.denominator_unit ? `${top}/${c.denominator_unit}` : top;
  }
  return "";
}

/** One ingredient as edited in the panel: a strength, optionally "per" a volume or mass. */
export type ComponentRow = { ingredient: string; value: string; unit: string; perValue: string; perUnit: string };

/** The editable form of a structure (always at least one row). */
export type StructureForm = {
  rows: ComponentRow[];
  doseForm: string;
  brand: string;
  quantityValue: string;
  quantityUnit: string;
};

export const emptyRow = (): ComponentRow => ({ ingredient: "", value: "", unit: "", perValue: "", perUnit: "" });

export function toForm(s: DrugStructure | null | undefined): StructureForm {
  const rows = (s?.components ?? []).map((c) =>
    c.numerator_value != null || c.denominator_unit
      ? {
          ingredient: c.ingredient,
          value: num(c.numerator_value),
          unit: c.numerator_unit ?? "",
          perValue: num(c.denominator_value),
          perUnit: c.denominator_unit ?? "",
        }
      : { ingredient: c.ingredient, value: num(c.amount_value), unit: c.amount_unit ?? "", perValue: "", perUnit: "" },
  );
  return {
    rows: rows.length ? rows : [emptyRow()],
    doseForm: s?.dose_form ?? "",
    brand: s?.brand_name ?? "",
    quantityValue: num(s?.quantity_value),
    quantityUnit: s?.quantity_unit ?? "",
  };
}

/** A positive number typed with a dot or a decimal comma, else null. */
export function parseNumber(raw: string): number | null {
  const v = Number(raw.trim().replace(",", "."));
  return raw.trim() !== "" && Number.isFinite(v) && v > 0 ? v : null;
}

/** The structure sent for matching; null when no ingredient (and no brand) is filled in. */
export function fromForm(f: StructureForm): DrugStructure | null {
  const text = (v: string) => v.trim() || null;
  const components: DrugComponent[] = f.rows
    .filter((r) => r.ingredient.trim())
    .map((r) => {
      const value = parseNumber(r.value);
      const per = r.perUnit.trim() || r.perValue.trim();
      return per
        ? {
            ingredient: r.ingredient.trim(),
            numerator_value: value,
            numerator_unit: text(r.unit),
            denominator_value: parseNumber(r.perValue),
            denominator_unit: text(r.perUnit),
          }
        : { ingredient: r.ingredient.trim(), amount_value: value, amount_unit: text(r.unit) };
    });
  if (!components.length && !f.brand.trim()) return null;
  const quantity = parseNumber(f.quantityValue);
  return {
    components,
    dose_form: text(f.doseForm),
    brand_name: text(f.brand),
    quantity_value: quantity,
    quantity_unit: quantity != null ? text(f.quantityUnit) : null,
  };
}
