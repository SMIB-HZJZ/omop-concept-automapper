import { describe, expect, it } from "vitest";

import { methodLabel } from "./methods";

describe("methodLabel", () => {
  it("names known methods and keeps unknown codes", () => {
    expect(methodLabel("auto_final")).toBe("Final decision");
    expect(methodLabel("auto_agentic")).toBe("Agent retry");
    expect(methodLabel("plugin_step")).toBe("plugin_step");
    expect(methodLabel(null)).toBe("—");
  });

  it("names pipeline steps", async () => {
    const { stepName } = await import("./methods");
    expect(stepName("decide")).toBe("Final decision on all evidence");
    expect(stepName("custom_node")).toBe("custom_node");
  });
});
