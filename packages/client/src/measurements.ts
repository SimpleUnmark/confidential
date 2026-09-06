/** Must match the Python workload's capability measurement checks. */
export const countWords = (text: string) =>
  text.match(/[\p{L}\p{M}\p{N}]+(?:['’][\p{L}\p{M}\p{N}]+)*/gu)?.length ?? 0;

export const countCharacters = (text: string) => Array.from(text).length;
export const countInputBytes = (text: string) => new TextEncoder().encode(text).length;
