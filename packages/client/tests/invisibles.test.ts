import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { countWords } from "../src/measurements";
import {
  cleanInvisibles,
  describeCodePoint,
  formatCodePoint,
  inspectInvisibles,
  scanInvisibles,
  supportedCodePoints,
} from "../src/invisibles";

type CleaningFixture = {
  name: string;
  input: string;
  text: string;
  removed: number;
  normalizedSpaces: number;
  words: number;
};

const sharedFixtures = JSON.parse(
  readFileSync(new URL("../../../fixtures/deterministic-cleaning.json", import.meta.url), "utf8"),
) as CleaningFixture[];

describe("invisible character inspector", () => {
  it("names every code point it finds and locates it", () => {
    const report = inspectInvisibles("user\u200Bname\u00A0= 1");

    expect(report.total).toBe(2);
    expect(report.findings).toHaveLength(2);

    const zeroWidth = report.findings.find((finding) => finding.codePoint === 0x200b)!;
    expect(zeroWidth.name).toBe("Zero width space");
    expect(zeroWidth.action).toBe("remove");
    expect(zeroWidth.positions).toEqual([{ line: 1, column: 5 }]);

    const nbsp = report.findings.find((finding) => finding.codePoint === 0x00a0)!;
    expect(nbsp.action).toBe("normalise");
    expect(nbsp.group).toBe("space");
  });

  it("counts repeats and tracks line and column across newlines", () => {
    const report = inspectInvisibles("a\u200Bb\nc\u200Bd");
    const finding = report.findings[0];

    expect(finding.count).toBe(2);
    expect(finding.positions).toEqual([
      { line: 1, column: 2 },
      { line: 2, column: 2 },
    ]);
  });

  it("reports nothing for clean text", () => {
    expect(inspectInvisibles("Perfectly ordinary text.")).toEqual({ findings: [], total: 0 });
    expect(inspectInvisibles("")).toEqual({ findings: [], total: 0 });
  });

  it("describes tag characters as a range without listing each one", () => {
    expect(describeCodePoint(0xe0001).name).toBe("Language tag");
    expect(describeCodePoint(0xe007f).name).toBe("Cancel tag");
    expect(describeCodePoint(0xe0041).group).toBe("tag");
    expect(inspectInvisibles("hi\u{E0041}").findings[0].action).toBe("remove");
  });

  it("formats code points the way the Unicode standard writes them", () => {
    expect(formatCodePoint(0x200b)).toBe("U+200B");
    expect(formatCodePoint(0x00ad)).toBe("U+00AD");
    expect(formatCodePoint(0xe0001)).toBe("U+E0001");
  });
});

describe("browser-side cleanup", () => {
  it("matches the shared browser/Python fixtures", () => {
    for (const fixture of sharedFixtures) {
      expect(cleanInvisibles(fixture.input), fixture.name).toEqual({
        text: fixture.text,
        removed: fixture.removed,
        normalized: fixture.normalizedSpaces,
      });
      expect(countWords(fixture.input), fixture.name).toBe(fixture.words);
    }
  });

  it("removes and normalises the documented character classes", () => {
    expect(cleanInvisibles("Hello\u200B\u2060 world\u00A0again")).toEqual({
      text: "Hello world again",
      removed: 2,
      normalized: 1,
    });
  });

  it("agrees with the summary scan on totals", () => {
    const sample = "a\u200Bb\u202Ec\u00A0d\u00ADe";
    const summary = scanInvisibles(sample);
    const detailed = inspectInvisibles(sample);
    expect(detailed.total).toBe(summary.total);
  });

  /* This is the claim the methodology page makes in public: an aggressive
     cleaner that strips joiners silently corrupts legitimate text. */
  it("preserves joiners that emoji and several writing systems require", () => {
    const cases = [
      "\u{1F468}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}", // family, ZWJ sequence
      "\u{1F3F3}\uFE0F\u200D\u{1F308}", // rainbow flag
      "می\u200Cخوانم", // Persian, ZWNJ
      "क्\u200Dष", // Devanagari, ZWJ
    ];

    for (const sample of cases) {
      expect(cleanInvisibles(sample).text).toBe(sample);
      expect(inspectInvisibles(sample).total).toBe(0);
    }
  });

  it("uses v0.7 context rules for bidi, flag tags, and invalid joiners", () => {
    const scotland = "🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}";
    const preserved = `\u2066RTL\u2069 \u202Aembedded\u202C ©️ ${scotland}`;

    expect(cleanInvisibles(preserved).text).toBe(preserved);
    expect(cleanInvisibles("🏴\u{E0067}")).toEqual({ text: "🏴", removed: 1, normalized: 0 });
    expect(cleanInvisibles("Latin\u200Dtext")).toEqual({ text: "Latintext", removed: 1, normalized: 0 });
  });

  it("removes v0.7 reserved carriers, noncharacters, and private-use text", () => {
    expect(cleanInvisibles("A\u2065B\uFFF0C\uFDD0D\uE000E")).toEqual({
      text: "ABCDE",
      removed: 4,
      normalized: 0,
    });
  });
});

describe("published coverage table", () => {
  it("matches what the cleaner actually does, entry by entry", () => {
    for (const entry of supportedCodePoints) {
      const character = String.fromCodePoint(entry.codePoint);
      const result = cleanInvisibles(`a${character}b`);

      if (entry.action === "remove") {
        expect(result.text, `${formatCodePoint(entry.codePoint)} should be removed`).toBe("ab");
        expect(result.removed).toBe(1);
      } else {
        expect(result.text, `${formatCodePoint(entry.codePoint)} should become a space`).toBe("a b");
        expect(result.normalized).toBe(1);
      }
    }
  });

  it("documents every entry with a name and a reason", () => {
    expect(supportedCodePoints.length).toBeGreaterThan(40);
    for (const entry of supportedCodePoints) {
      expect(entry.name.length).toBeGreaterThan(3);
      expect(entry.note.length).toBeGreaterThan(20);
    }
  });

  it("documents joiners as context-aware coverage", () => {
    const codePoints = supportedCodePoints.map((entry) => entry.codePoint);
    expect(codePoints).toContain(0x200c);
    expect(codePoints).toContain(0x200d);
  });
});
