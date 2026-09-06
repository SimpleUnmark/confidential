/**
 * Browser-side port of watermarks-remover v0.7.0 Layer A. The shared fixtures
 * pin this implementation against the vendored Python cleaner.
 */

export const UNUSUAL_SPACES = /[\u{A0}\u{1680}\u{2000}-\u{200A}\u{202F}\u{205F}\u{3000}]/gu;

export type InvisibleGroup =
  | "zero-width"
  | "direction"
  | "soft-hyphen"
  | "variation"
  | "tag"
  | "reserved"
  | "space";

export const invisibleGroupLabels: Record<InvisibleGroup, { short: string; description: string }> = {
  "zero-width": { short: "zero-width", description: "Zero-width spaces, joiners and formatting controls" },
  direction: { short: "direction control", description: "Bidirectional embedding and override controls" },
  "soft-hyphen": { short: "soft hyphen", description: "Discretionary hyphens that only appear at a line break" },
  variation: { short: "variation control", description: "Selectors and fillers that can alter script or emoji rendering" },
  tag: { short: "tag character", description: "Unicode tag characters, sometimes used to hide text in plain sight" },
  reserved: { short: "reserved/private", description: "Noncharacters and invisible reserved or private-use code points" },
  space: { short: "unusual space", description: "Non-breaking and typographic spaces normalised to a plain space" },
};

const GROUP_ORDER: InvisibleGroup[] = [
  "zero-width", "direction", "soft-hyphen", "variation", "tag", "reserved", "space",
];

const SPACE_CODE_POINTS = new Set([
  0x00a0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
  0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x202f, 0x205f, 0x3000,
]);

const STRIP_CODE_POINTS = new Set([
  0x00ad, 0x034f, 0x061c, 0x115f, 0x1160, 0x17b4, 0x17b5, 0x180b,
  0x180c, 0x180d, 0x180e, 0x180f, 0x200b, 0x200c, 0x200d, 0x200e,
  0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2060, 0x2061,
  0x2062, 0x2063, 0x2064, 0x2066, 0x2067, 0x2068, 0x2069, 0x206a,
  0x206b, 0x206c, 0x206d, 0x206e, 0x206f, 0xfeff, 0xfe00, 0xfe01,
  0xfe02, 0xfe03, 0xfe04, 0xfe05, 0xfe06, 0xfe07, 0xfe08, 0xfe09,
  0xfe0a, 0xfe0b, 0xfe0c, 0xfe0d, 0xfe0e, 0xfe0f, 0x3164, 0xffa0,
  0xfff9, 0xfffa, 0xfffb,
]);

const PRESERVABLE_BIDI = new Set([0x061c, 0x200e, 0x200f, 0x2066, 0x2067, 0x2068, 0x2069]);
const MONGOLIAN_FVS = new Set([0x180b, 0x180c, 0x180d, 0x180f]);
const KHMER_VOWELS = new Set([0x17b4, 0x17b5]);
const HANGUL_FILLERS = new Set([0x115f, 0x1160, 0x3164, 0xffa0]);
const ORTHOGRAPHIC_CF = new Set([
  0x0600, 0x0601, 0x0602, 0x0603, 0x0604, 0x0605, 0x06dd, 0x070f,
  0x08e2, 0x110bd, 0x110cd,
]);

const codePointOf = (character: string) => character.codePointAt(0)!;
const inRange = (value: number, start: number, endExclusive: number) => value >= start && value < endExclusive;

const isReservedIgnorable = (codePoint: number) =>
  codePoint === 0x2065 ||
  codePoint === 0xe0000 ||
  inRange(codePoint, 0xfff0, 0xfff9) ||
  inRange(codePoint, 0xe0080, 0xe0100) ||
  inRange(codePoint, 0xe01f0, 0xe1000);

const isNoncharacter = (codePoint: number) =>
  inRange(codePoint, 0xfdd0, 0xfdf0) || (codePoint & 0xfffe) === 0xfffe;

const isPrivateUse = (codePoint: number) =>
  inRange(codePoint, 0xe000, 0xf900) ||
  inRange(codePoint, 0xf0000, 0xffffe) ||
  inRange(codePoint, 0x100000, 0x10fffe);

const isVariationSelector = (codePoint: number) =>
  MONGOLIAN_FVS.has(codePoint) ||
  inRange(codePoint, 0xfe00, 0xfe10) ||
  inRange(codePoint, 0xe0100, 0xe01f0);

const isStripCodePoint = (codePoint: number) =>
  STRIP_CODE_POINTS.has(codePoint) ||
  inRange(codePoint, 0xe0001, 0xe0080) ||
  isVariationSelector(codePoint) ||
  isNoncharacter(codePoint) ||
  isReservedIgnorable(codePoint) ||
  isPrivateUse(codePoint);

const isEmojiBase = (codePoint: number) =>
  inRange(codePoint, 0x1f000, 0x1fb00) ||
  inRange(codePoint, 0x2190, 0x2600) ||
  inRange(codePoint, 0x2600, 0x27c0) ||
  inRange(codePoint, 0x2b00, 0x2c00) ||
  [0x203c, 0x2049, 0x2139, 0x2934, 0x2935, 0x00a9, 0x00ae, 0x2122, 0x3030, 0x303d, 0x3297, 0x3299, 0x0023, 0x002a].includes(codePoint) ||
  inRange(codePoint, 0x0030, 0x003a);

const isCjkIdeograph = (codePoint: number) =>
  inRange(codePoint, 0x3400, 0x4dc0) ||
  inRange(codePoint, 0x4e00, 0xa000) ||
  inRange(codePoint, 0xf900, 0xfb00) ||
  inRange(codePoint, 0x20000, 0x323b0);

const isLetterOrMark = (character: string) => /[\p{L}\p{M}]/u.test(character);

const joiningScript = (character: string): string | undefined => {
  if (!isLetterOrMark(character)) return undefined;
  const codePoint = codePointOf(character);
  if (inRange(codePoint, 0x0600, 0x0900)) return "arabic";
  if (inRange(codePoint, 0x0900, 0x0e00)) return "indic";
  if (inRange(codePoint, 0x0f00, 0x10a0)) return "south-asian";
  if (inRange(codePoint, 0x1780, 0x1800)) return "khmer";
  if (inRange(codePoint, 0x1800, 0x18b0)) return "mongolian";
  return undefined;
};

const isMongolianLetter = (character: string) =>
  inRange(codePointOf(character), 0x1800, 0x18b0) && /\p{L}/u.test(character);
const isKhmerLetter = (character: string) =>
  inRange(codePointOf(character), 0x1780, 0x1800) && /\p{L}/u.test(character);
const isHangulJamo = (codePoint: number) =>
  inRange(codePoint, 0x1100, 0x1200) ||
  inRange(codePoint, 0xa960, 0xa97d) ||
  inRange(codePoint, 0xd7b0, 0xd7c7) ||
  inRange(codePoint, 0x3131, 0x318f) ||
  inRange(codePoint, 0xffa1, 0xffdd);

const layoutScript = (codePoint: number): [number, number] | undefined => {
  if (inRange(codePoint, 0x13430, 0x13440)) return [0x13000, 0x14400];
  if (inRange(codePoint, 0x1bca0, 0x1bca4)) return [0x1bc00, 0x1bca4];
  if (inRange(codePoint, 0x1d173, 0x1d17b)) return [0x1d100, 0x1d200];
  return undefined;
};

const isGlue = (codePoint: number) =>
  codePoint === 0x200d ||
  codePoint === 0xfe0e ||
  codePoint === 0xfe0f ||
  codePoint === 0x200c ||
  isVariationSelector(codePoint) ||
  inRange(codePoint, 0xe0020, 0xe0080) ||
  KHMER_VOWELS.has(codePoint) ||
  HANGUL_FILLERS.has(codePoint);

const validFlagTagIndices = (characters: string[]) => {
  const valid = new Set<number>();
  let index = 0;
  while (index < characters.length) {
    if (codePointOf(characters[index]) !== 0x1f3f4) {
      index += 1;
      continue;
    }
    let cursor = index + 1;
    while (cursor < characters.length && inRange(codePointOf(characters[cursor]), 0xe0020, 0xe007f)) cursor += 1;
    if (cursor > index + 1 && cursor < characters.length && codePointOf(characters[cursor]) === 0xe007f) {
      for (let tagIndex = index + 1; tagIndex <= cursor; tagIndex += 1) valid.add(tagIndex);
      index = cursor + 1;
    } else index += 1;
  }
  return valid;
};

const validBidiEmbeddingIndices = (characters: string[]) => {
  const valid = new Set<number>();
  const stack: { opener: number; index: number }[] = [];
  characters.forEach((character, index) => {
    const codePoint = codePointOf(character);
    if ([0x202a, 0x202b, 0x202d, 0x202e].includes(codePoint)) {
      stack.push({ opener: codePoint, index });
    } else if (codePoint === 0x202c) {
      const pair = stack.pop();
      if (pair && (pair.opener === 0x202a || pair.opener === 0x202b)) {
        valid.add(pair.index);
        valid.add(index);
      }
    }
  });
  return valid;
};

type Decision = { action: "keep" | "remove" | "normalise"; output: string };

const decide = (
  character: string,
  previousKept: string | undefined,
  previousInput: string | undefined,
  nextInput: string | undefined,
  validFlagTag: boolean,
  validBidiEmbedding: boolean,
): Decision => {
  const codePoint = codePointOf(character);
  if (validBidiEmbedding || PRESERVABLE_BIDI.has(codePoint)) return { action: "keep", output: character };

  if (previousInput) {
    const previousCodePoint = codePointOf(previousInput);
    if (inRange(codePoint, 0xe0100, 0xe01f0) && isCjkIdeograph(previousCodePoint)) return { action: "keep", output: character };
    if (MONGOLIAN_FVS.has(codePoint) && inRange(previousCodePoint, 0x1800, 0x18b0)) return { action: "keep", output: character };
    if (inRange(codePoint, 0xfe00, 0xfe0e) && isCjkIdeograph(previousCodePoint)) return { action: "keep", output: character };
    if ((codePoint === 0xfe0e || codePoint === 0xfe0f) && isEmojiBase(previousCodePoint)) return { action: "keep", output: character };
  }

  if (
    codePoint === 0x200d && previousKept && nextInput &&
    isEmojiBase(codePointOf(previousKept)) && isEmojiBase(codePointOf(nextInput))
  ) return { action: "keep", output: character };

  if ((codePoint === 0x200c || codePoint === 0x200d) && previousInput && nextInput) {
    const previousScript = joiningScript(previousInput);
    if (previousScript && previousScript === joiningScript(nextInput)) return { action: "keep", output: character };
  }
  if (inRange(codePoint, 0xe0020, 0xe0080) && validFlagTag) return { action: "keep", output: character };
  if (MONGOLIAN_FVS.has(codePoint) && previousKept && isMongolianLetter(previousKept)) return { action: "keep", output: character };
  if (KHMER_VOWELS.has(codePoint) && previousKept && isKhmerLetter(previousKept)) return { action: "keep", output: character };
  if (HANGUL_FILLERS.has(codePoint) && previousKept && isHangulJamo(codePointOf(previousKept))) return { action: "keep", output: character };
  if (ORTHOGRAPHIC_CF.has(codePoint)) return { action: "keep", output: character };

  const script = layoutScript(codePoint);
  if (script && (
    (previousInput && inRange(codePointOf(previousInput), script[0], script[1])) ||
    (nextInput && inRange(codePointOf(nextInput), script[0], script[1]))
  )) return { action: "keep", output: character };

  if (isStripCodePoint(codePoint)) return { action: "remove", output: "" };
  if (SPACE_CODE_POINTS.has(codePoint)) return { action: "normalise", output: " " };
  if (/\p{Cf}/u.test(character)) return { action: "remove", output: "" };
  return { action: "keep", output: character };
};

type AnalysedCharacter = { character: string; codePoint: number; decision: Decision };

const analyse = (input: string): AnalysedCharacter[] => {
  const characters = Array.from(input);
  const flagTags = validFlagTagIndices(characters);
  const bidiEmbeddings = validBidiEmbeddingIndices(characters);
  const analysed: AnalysedCharacter[] = [];
  let previousKept: string | undefined;

  characters.forEach((character, index) => {
    const decision = decide(
      character, previousKept, characters[index - 1], characters[index + 1],
      flagTags.has(index), bidiEmbeddings.has(index),
    );
    const codePoint = codePointOf(character);
    analysed.push({ character, codePoint, decision });
    if (decision.action === "normalise" || (decision.action === "keep" && !isGlue(codePoint))) {
      previousKept = decision.output;
    }
  });
  return analysed;
};

const groupFor = (codePoint: number): InvisibleGroup => {
  if (codePoint === 0x00ad) return "soft-hyphen";
  if (inRange(codePoint, 0xe0001, 0xe0080)) return "tag";
  if (
    codePoint === 0x061c || codePoint === 0x200e || codePoint === 0x200f ||
    inRange(codePoint, 0x202a, 0x202f) || inRange(codePoint, 0x2066, 0x2070)
  ) return "direction";
  if (isVariationSelector(codePoint) || KHMER_VOWELS.has(codePoint) || HANGUL_FILLERS.has(codePoint)) return "variation";
  if (isReservedIgnorable(codePoint) || isNoncharacter(codePoint) || isPrivateUse(codePoint)) return "reserved";
  return "zero-width";
};

export type InvisibleScan = {
  removable: number;
  spaces: number;
  total: number;
  groups: { group: InvisibleGroup; count: number }[];
};

const EMPTY_SCAN: InvisibleScan = { removable: 0, spaces: 0, total: 0, groups: [] };

export function scanInvisibles(input: string): InvisibleScan {
  if (!input) return EMPTY_SCAN;
  const counts = new Map<InvisibleGroup, number>();
  let removable = 0;
  let spaces = 0;
  for (const { codePoint, decision } of analyse(input)) {
    if (decision.action === "keep") continue;
    if (decision.action === "remove") removable += 1;
    else spaces += 1;
    const group = decision.action === "normalise" ? "space" : groupFor(codePoint);
    counts.set(group, (counts.get(group) ?? 0) + 1);
  }
  return {
    removable,
    spaces,
    total: removable + spaces,
    groups: GROUP_ORDER.flatMap((group) => counts.has(group) ? [{ group, count: counts.get(group)! }] : []),
  };
}

export type CodePointInfo = {
  codePoint: number;
  name: string;
  group: InvisibleGroup;
  action: "remove" | "normalise";
  note: string;
};

const CATALOG_ENTRIES: [number, string, string][] = [
  [0x00ad, "Soft hyphen", "Marks where a word may break across lines; invisible unless the break happens."],
  [0x034f, "Combining grapheme joiner", "Affects how combining marks are ordered and collated."],
  [0x115f, "Hangul choseong filler", "Holds a Hangul jamo slot after compatible Hangul text; isolated uses are removed."],
  [0x1160, "Hangul jungseong filler", "Holds a Hangul jamo slot after compatible Hangul text; isolated uses are removed."],
  [0x17b4, "Khmer vowel inherent AQ", "An invisible Khmer vowel preserved after a Khmer letter and removed elsewhere."],
  [0x17b5, "Khmer vowel inherent AA", "An invisible Khmer vowel preserved after a Khmer letter and removed elsewhere."],
  [0x180b, "Mongolian free variation selector one", "Selects a Mongolian glyph form after Mongolian text; isolated uses are removed."],
  [0x180c, "Mongolian free variation selector two", "Selects a Mongolian glyph form after Mongolian text; isolated uses are removed."],
  [0x180d, "Mongolian free variation selector three", "Selects a Mongolian glyph form after Mongolian text; isolated uses are removed."],
  [0x180e, "Mongolian vowel separator", "A formatting control reclassified by Unicode; rarely intentional in pasted prose."],
  [0x180f, "Mongolian free variation selector four", "Selects a Mongolian glyph form after Mongolian text; isolated uses are removed."],
  [0x200b, "Zero width space", "Allows a line break without a visible space; frequently used as an edit-based carrier."],
  [0x200c, "Zero width non-joiner", "Preserved between letters of the same joining script; invalid or isolated uses are removed."],
  [0x200d, "Zero width joiner", "Preserved in valid emoji and same-script sequences; invalid or isolated uses are removed."],
  [0x202a, "Left-to-right embedding", "Complete embedding pairs are preserved; an unpaired opener is removed."],
  [0x202b, "Right-to-left embedding", "Complete embedding pairs are preserved; an unpaired opener is removed."],
  [0x202c, "Pop directional formatting", "Closes a valid embedding; an unpaired closer is removed."],
  [0x202d, "Left-to-right override", "Forces display order and can disguise text, so it is removed."],
  [0x202e, "Right-to-left override", "Can reverse displayed order, a known spoofing technique in filenames."],
  [0x2060, "Word joiner", "Prevents a line break without adding width."],
  [0x2061, "Function application", "Invisible mathematical operator removed by the prose cleaner."],
  [0x2062, "Invisible times", "Invisible multiplication operator removed by the prose cleaner."],
  [0x2063, "Invisible separator", "Invisible comma removed by the prose cleaner."],
  [0x2064, "Invisible plus", "Invisible addition operator removed by the prose cleaner."],
  [0x2065, "Reserved format control", "A permanently invisible reserved code point with no interchange-text use."],
  [0x206a, "Inhibit symmetric swapping", "Deprecated bidirectional formatting control."],
  [0x206b, "Activate symmetric swapping", "Deprecated bidirectional formatting control."],
  [0x206c, "Inhibit Arabic form shaping", "Deprecated bidirectional formatting control."],
  [0x206d, "Activate Arabic form shaping", "Deprecated bidirectional formatting control."],
  [0x206e, "National digit shapes", "Deprecated bidirectional formatting control."],
  [0x206f, "Nominal digit shapes", "Deprecated bidirectional formatting control."],
  [0x3164, "Hangul filler", "A blank compatibility jamo preserved after compatible Hangul and removed elsewhere."],
  [0xfe00, "Variation selector one", "Preserved after a compatible CJK base and removed when it floats in prose."],
  [0xfe0f, "Variation selector sixteen", "Preserved after an emoji base and removed when it floats in prose."],
  [0xfeff, "Zero width no-break space", "A byte-order marker that often survives into pasted text."],
  [0xffa0, "Halfwidth Hangul filler", "A blank halfwidth jamo preserved after compatible Hangul and removed elsewhere."],
  [0xfff0, "Reserved default-ignorable code point", "Reserved for future invisible formatting and invalid in current interchange text."],
  [0xfff9, "Interlinear annotation anchor", "An invisible annotation control removed from ordinary prose."],
  [0xfffa, "Interlinear annotation separator", "An invisible annotation control removed from ordinary prose."],
  [0xfffb, "Interlinear annotation terminator", "An invisible annotation control removed from ordinary prose."],
  [0xfdd0, "Unicode noncharacter", "Permanently reserved for internal use and prohibited in interchange text."],
  [0xe000, "Private-use character", "Has no portable meaning outside a private agreement or custom font."],
  [0xe0000, "Reserved tag code point", "A default-ignorable reserved code point with no interchange-text use."],
  [0xe0080, "Reserved tag-block code point", "A default-ignorable reserved code point with no interchange-text use."],
  [0xe01f0, "Reserved variation-selector code point", "A default-ignorable reserved code point with no interchange-text use."],
  [0x00a0, "No-break space", "A space that prevents a line break and can break string comparison."],
  [0x1680, "Ogham space mark", "A visible-width space character from the Ogham block."],
  [0x2000, "En quad", "A fixed-width typographic space."],
  [0x2001, "Em quad", "A fixed-width typographic space."],
  [0x2002, "En space", "A fixed-width typographic space."],
  [0x2003, "Em space", "A fixed-width typographic space."],
  [0x2004, "Three-per-em space", "A fixed-width typographic space."],
  [0x2005, "Four-per-em space", "A fixed-width typographic space."],
  [0x2006, "Six-per-em space", "A fixed-width typographic space."],
  [0x2007, "Figure space", "A space matching digit width, used in tabular numbers."],
  [0x2008, "Punctuation space", "A space matching punctuation width."],
  [0x2009, "Thin space", "A narrow typographic space common in exported documents."],
  [0x200a, "Hair space", "The narrowest typographic space."],
  [0x202f, "Narrow no-break space", "A narrow non-breaking space common in French typography."],
  [0x205f, "Medium mathematical space", "A space used in mathematical notation."],
  [0x3000, "Ideographic space", "The full-width space used in CJK text."],
];

const catalog = new Map<number, CodePointInfo>(
  CATALOG_ENTRIES.map(([codePoint, name, note]) => [codePoint, {
    codePoint,
    name,
    note,
    group: SPACE_CODE_POINTS.has(codePoint) ? "space" : groupFor(codePoint),
    action: SPACE_CODE_POINTS.has(codePoint) ? "normalise" : "remove",
  }]),
);

export const supportedCodePoints = [...catalog.values()].sort((a, b) => a.codePoint - b.codePoint);

export const formatCodePoint = (codePoint: number) =>
  `U+${codePoint.toString(16).toUpperCase().padStart(4, "0")}`;

export function describeCodePoint(codePoint: number): CodePointInfo {
  const known = catalog.get(codePoint);
  if (known) return known;
  if (inRange(codePoint, 0xe0001, 0xe0080)) return {
    codePoint,
    name: codePoint === 0xe0001 ? "Language tag" : codePoint === 0xe007f ? "Cancel tag" : "Tag character",
    group: "tag",
    action: "remove",
    note: "Preserved only inside a complete subdivision-flag sequence; otherwise it can encode hidden text.",
  };
  if (isVariationSelector(codePoint)) return {
    codePoint,
    name: "Variation selector",
    group: "variation",
    action: "remove",
    note: "Preserved after a compatible script or emoji base and removed when it floats in prose.",
  };
  if (isNoncharacter(codePoint) || isReservedIgnorable(codePoint) || isPrivateUse(codePoint)) return {
    codePoint,
    name: isNoncharacter(codePoint) ? "Unicode noncharacter" : isPrivateUse(codePoint) ? "Private-use character" : "Reserved default-ignorable code point",
    group: "reserved",
    action: "remove",
    note: "This code point has no portable meaning in ordinary interchange text.",
  };
  return {
    codePoint,
    name: "Invisible formatting character",
    group: groupFor(codePoint),
    action: "remove",
    note: "A formatting control with no visible glyph in this context.",
  };
}

export type Finding = CodePointInfo & {
  count: number;
  positions: { line: number; column: number }[];
};

const POSITION_LIMIT = 6;

export function inspectInvisibles(input: string): { findings: Finding[]; total: number } {
  if (!input) return { findings: [], total: 0 };
  const found = new Map<number, Finding>();
  let line = 1;
  let column = 1;
  let total = 0;

  for (const { character, codePoint, decision } of analyse(input)) {
    if (character === "\n") {
      line += 1;
      column = 1;
      continue;
    }
    if (decision.action !== "keep") {
      total += 1;
      const existing = found.get(codePoint);
      if (existing) {
        existing.count += 1;
        if (existing.positions.length < POSITION_LIMIT) existing.positions.push({ line, column });
      } else {
        found.set(codePoint, {
          ...describeCodePoint(codePoint),
          action: decision.action,
          count: 1,
          positions: [{ line, column }],
        });
      }
    }
    column += 1;
  }

  return {
    findings: [...found.values()].sort((a, b) => b.count - a.count || a.codePoint - b.codePoint),
    total,
  };
}

export function cleanInvisibles(input: string): { text: string; removed: number; normalized: number } {
  let removed = 0;
  let normalized = 0;
  const output = analyse(input).map(({ decision }) => {
    if (decision.action === "remove") removed += 1;
    if (decision.action === "normalise") normalized += 1;
    return decision.output;
  });
  return { text: output.join(""), removed, normalized };
}
