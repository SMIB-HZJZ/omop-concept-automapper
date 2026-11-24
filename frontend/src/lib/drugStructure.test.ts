import { describe, expect, it } from "vitest";

import { formatStrength, fromForm, parseNumber, toForm } from "./drugStructure";

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
