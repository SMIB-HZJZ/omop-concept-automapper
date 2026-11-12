import { describe, expect, it } from "vitest";

import { formatBytes, formatDate, formatNumber } from "./format";

describe("formatters", () => {
  it("formats missing numbers as 0", () => {
    expect(formatNumber(null)).toBe((0).toLocaleString());
    expect(formatNumber(undefined)).toBe((0).toLocaleString());
  });

  it("picks a byte unit", () => {
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 ** 3)).toBe("3.00 GB");
  });

  it("formats dates and passes through what it cannot parse", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("not a date");
    expect(formatDate("2026-01-02T03:04:05Z")).toBe(new Date("2026-01-02T03:04:05Z").toLocaleString());
  });
});
