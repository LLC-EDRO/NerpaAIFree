const headerKey = (s: string) =>
  s.toLocaleLowerCase().replace(/[\s.,:]+/gu, "");
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Recognize explicit prose tables (ordered headers followed by labelled rows).
 * Used before native cells exist, during plan adaptation. A naked a/b remains
 * a fraction; only a complete row matching a textual header supplies cells. */
export function tableColumnEvidence(text: string): string[] {
  let columns = 0;
  const result: string[] = [];
  for (const segment of text.split(/[;\n]|(?<!\d)\.(?!\d)/u)) {
    const colon = segment.lastIndexOf(":");
    const parts = segment.slice(colon + 1).split("/").map(s => s.trim());
    if (parts.length >= 3 && parts.length <= 16 && parts.every(s => /\p{L}/u.test(s) && !/\d/.test(s))) {
      columns = parts.length;
      continue;
    }
    const label = segment.slice(0, colon).trim();
    if (columns && colon >= 0 && /\p{L}/u.test(label) && parts.length === columns - 1 && parts.every(Boolean)) {
      result.push(...parts);
    } else columns = 0;
  }
  return result;
}

/** A slash between explicitly described table columns is not a fraction.
 * Require the same ordered headers, row label and column count. Ambiguous
 * prose, reordered headings and real fractions keep the normal validator. */
export function tabularCellEvidence(
  slot: any,
  slots: any[],
  fields: Record<string, { text: string }>,
  quotes: string[],
) {
  if (!slot.cell || slot.cell[0] < 1 || slot.cell[1] < 1) return undefined;
  const headers = slots
    .filter((s) => s.shapeId === slot.shapeId && s.cell?.[0] === 0)
    .sort((a, b) => a.cell[1] - b.cell[1]);
  if (headers.length < 3 || !headers.every((s, i) => s.cell[1] === i))
    return undefined;
  const expected = headers.map((s) => fields[s.key]?.text || "");
  if (expected.some((s) => !s.trim())) return undefined;
  const labelSlot = slots.find(
    (s) =>
      s.shapeId === slot.shapeId &&
      s.cell?.[0] === slot.cell[0] &&
      s.cell[1] === 0,
  );
  const label = fields[labelSlot?.key]?.text.trim();
  if (!label || label.length > 200 || !/\p{L}/u.test(label)) return undefined;
  const values = new Set<string>();
  for (const quote of quotes) {
    if (!headerKey(quote).includes(expected.map(headerKey).join("/"))) continue;
    const pattern = new RegExp(
      `(?:^|[\\s.;])${escape(label).replace(/\s+/g, "\\s+")}\\s*:\\s*([^;\\n]+)`,
      "giu",
    );
    for (const match of quote.matchAll(pattern)) {
      const row = match[1].split("/").map((s) => s.trim());
      if (row.length === headers.length - 1) values.add(row[slot.cell[1] - 1]);
    }
  }
  return values.size === 1 ? [...values][0] : undefined;
}
