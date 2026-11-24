import { describe, expect, it } from "vitest";

import { checkForm, formatStrength, fromForm, parseNumber, toForm } from "./drugStructure";

describe("formatStrength", () => {
  it("writes amounts and concentrations", () => {
    expect(formatStrength({ ingredient: "a", amount_value: 500, amount_unit: "mg" })).toBe("500 mg");
    expect(
      formatStrength({
        ingredient: "d",
        numerator_value: 10,
        numerator_unit: "mg",
        denominator_value: 2,
        denominator_unit: "mL",
      }),
    ).toBe("10 mg / 2 mL");
    expect(formatStrength({ ingredient: "d", numerator_value: 5, numerator_unit: "mg", denominator_unit: "mL" })).toBe(
      "5 mg/mL",
    );
    expect(formatStrength({ ingredient: "x" })).toBe("");
  });
});

describe("structure form", () => {
  it("round-trips a concentration with a quantity", () => {
    const structure = {
      components: [
        {
          ingredient: "diazepam",
          numerator_value: 10,
          numerator_unit: "mg",
          denominator_value: 2,
          denominator_unit: "mL",
        },
      ],
      dose_form: "Injectable Solution",
      brand_name: null,
      quantity_value: 2,
      quantity_unit: "mL",
    };
    expect(fromForm(toForm(structure))).toEqual(structure);
  });

  it("reads decimal commas, drops empty rows and needs an ingredient or a brand", () => {
    const form = toForm(null);
    expect(fromForm(form)).toBeNull();
    form.rows[0] = { ingredient: " paracetamol ", value: "0,5", unit: "g", perValue: "", perUnit: "" };
    form.rows.push({ ingredient: "", value: "", unit: "", perValue: "", perUnit: "" });
    expect(fromForm(form)?.components).toEqual([{ ingredient: "paracetamol", amount_value: 0.5, amount_unit: "g" }]);
    expect(parseNumber("abc")).toBeNull();
    expect(parseNumber("-1")).toBeNull();
  });
});

describe("checkForm", () => {
  const row = (over: Partial<ReturnType<typeof toForm>["rows"][number]>) => ({
    ingredient: "paracetamol",
    value: "500",
    unit: "mg",
    perValue: "",
    perUnit: "",
    ...over,
  });
  const form = (rows: ReturnType<typeof row>[], extra: Partial<ReturnType<typeof toForm>> = {}) => ({
    ...toForm(null),
    rows,
    ...extra,
  });

  it("accepts a complete structure", () => {
    expect(checkForm(form([row({})]))).toEqual([]);
    expect(checkForm(form([row({ value: "10", perValue: "2", perUnit: "mL" })]))).toEqual([]);
    expect(checkForm(form([row({ value: "", unit: "" })]))).toEqual([]); // ingredient only
  });

  it("names numbers that are not numbers and missing units", () => {
    expect(checkForm(form([row({ value: "5oo" })]))).toEqual([
      'Strength of paracetamol: "5oo" is not a positive number',
    ]);
    expect(checkForm(form([row({ unit: "" })]))).toEqual(["Strength of paracetamol needs a unit"]);
    expect(checkForm(form([row({})], { quantityValue: "2" }))).toEqual(["Quantity needs a unit"]);
    expect(checkForm(form([row({ perValue: "5" })]))).toEqual(['"Per" of paracetamol needs a unit']);
  });

  it("needs something to match", () => {
    expect(checkForm(form([row({ ingredient: "", value: "", unit: "" })]))).toEqual(["Enter an ingredient or a brand"]);
    expect(checkForm(form([row({ ingredient: "" })]))).toContain("A strength without an ingredient");
  });
});
