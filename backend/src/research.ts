import { createHash } from "node:crypto";
import type { Brief, Outline } from "./domain.js";
export type ResearchSource = { id: string; url: string; title: string };
export type PresentationResearch = {
  stamp: string;
  searchedAt: string;
  model: string;
  status: "ready" | "empty";
  contextStamp?: string;
  searchedOutline?: string[];
  searchedRequirements?: string[];
  coverage?: {
    targetNumericFacts: number;
    foundNumericFacts: number;
    sufficient: boolean;
  };
  queries: string[];
  sources: ResearchSource[];
  evidence: { id: string; text: string; sourceIds: string[] }[];
};
// Adapted from the service's citation parser. No arbitrary URL downloads here.
export function publicResearchUrl(raw: unknown): string | undefined {
  if (typeof raw !== "string" || raw.length > 2048) return;
  try {
    const u = new URL(raw),
      h = u.hostname.toLowerCase();
    if (
      !["http:", "https:"].includes(u.protocol) ||
      u.username ||
      u.password ||
      u.port ||
      !h.includes(".") ||
      h === "localhost" ||
      /\.(?:localhost|local|internal|test|invalid)$/.test(h) ||
      /^\d+\.\d+\.\d+\.\d+$/.test(h) ||
      h.includes(":")
    )
      return;
    for (const key of [...u.searchParams.keys()])
      if (key.startsWith("utm_")) u.searchParams.delete(key);
    return u.href;
  } catch {
    return;
  }
}

/** Only paragraphs carrying actual provider citation annotations enter the
 * evidence pool. A URL merely printed by the model is not a verified citation. */
export function parseWebResearch(
  payload: any,
): Pick<PresentationResearch, "queries" | "sources" | "evidence" | "status"> {
  if (
    payload?.status !== "completed" ||
    !Array.isArray(payload.output) ||
    !payload.output.some(
      (o: any) => o.type === "web_search_call" && o.status === "completed",
    )
  )
    throw new Error("search_not_completed");
  const sources: ResearchSource[] = [],
    evidence: PresentationResearch["evidence"] = [],
    queries: string[] = [];
  for (const item of payload.output) {
    if (item.type === "web_search_call")
      for (const q of item.action?.queries ??
        (item.action?.query ? [item.action.query] : []))
        if (
          typeof q === "string" &&
          !queries.includes(q) &&
          queries.length < 12
        )
          queries.push(q.slice(0, 350));
    if (item.type !== "message") continue;
    for (const part of item.content ?? []) {
      if (part.type !== "output_text" || typeof part.text !== "string")
        continue;
      const text: string = part.text;
      const annotations = (
        Array.isArray(part.annotations) ? part.annotations : []
      ).filter(
        (a: any) =>
          a.type === "url_citation" &&
          publicResearchUrl(a.url) &&
          Number.isInteger(a.start_index) &&
          Number.isInteger(a.end_index) &&
          a.start_index >= 0 &&
          a.end_index > a.start_index &&
          a.end_index <= text.length,
      );
      for (const match of text.matchAll(/[^\n]+(?:\n(?!\n)[^\n]+)*/g)) {
        const start = match.index,
          end = start + match[0].length;
        const citations = annotations.filter(
          (a: any) => a.start_index >= start && a.start_index < end,
        );
        if (!citations.length) continue;
        const cleaned = match[0]
          .replace(/cite[^]+/g, "")
          .replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, "$1")
          .replace(/https?:\/\/\S+/g, "")
          .replace(/\*\*/g, "")
          .replace(/\s+/g, " ")
          .replace(/^[#*\s]+/, "")
          .trim();
        if (
          cleaned.length < 15 ||
          cleaned.length > 2200 ||
          evidence.length >= 40
        )
          continue;
        const sourceIds: string[] = [];
        for (const citation of citations) {
          const url = publicResearchUrl(citation.url)!;
          let source = sources.find((s) => s.url === url);
          if (!source && sources.length < 40) {
            source = {
              id: `source-${sources.length + 1}`,
              url,
              title: (typeof citation.title === "string"
                ? citation.title
                : new URL(url).hostname
              ).slice(0, 300),
            };
            sources.push(source);
          }
          if (source && !sourceIds.includes(source.id) && sourceIds.length < 12)
            sourceIds.push(source.id);
        }
        if (sourceIds.length)
          evidence.push({
            id: `evidence-${evidence.length + 1}`,
            text: cleaned,
            sourceIds,
          });
      }
    }
  }
  // Retain discovered attachments even when the hosted reader cannot parse
  // them and consequently emits no citation in its summary.
  for (const item of payload.output) {
    if (item.type !== "web_search_call") continue;
    const candidates = [
      ...(item.action?.sources ?? []),
      ...(item.action?.url ? [{ url: item.action.url }] : []),
    ];
    for (const candidate of candidates) {
      const url = publicResearchUrl(candidate?.url);
      if (url && !sources.some((s) => s.url === url) && sources.length < 40)
        sources.push({
          id: `source-${sources.length + 1}`,
          url,
          title: String(candidate.title ?? new URL(url).hostname).slice(0, 300),
        });
    }
  }
  return {
    sources,
    evidence,
    queries,
    status: evidence.length ? "ready" : "empty",
  };
}

export function researchStamp(brief: Brief) {
  return createHash("sha256")
    .update(
      JSON.stringify({
        version: 2,
        topic: brief.topic,
        ...(brief.instructions ? {instructions:brief.instructions}:{}),
        sourceText: brief.sourceText,
      }),
    )
    .digest("hex");
}
export function researchText(research?: PresentationResearch) {
  return research?.evidence.map((e) => e.text).join("\n\n") || "";
}
export function numericEvidenceCount(research?: PresentationResearch) {
  return research?.evidence.filter((e) => /\d/.test(e.text)).length || 0;
}
export function researchContext(
  outline?: Outline,
  requirements: unknown[] = [],
) {
  return createHash("sha256")
    .update(JSON.stringify({ version: 1, outline, requirements }))
    .digest("hex");
}

/** A completed bounded search is reusable even when sources are scarce.
 * Individual slide fields, not a global count quota, establish sufficiency. */
export function reusableResearch(
  research: PresentationResearch | undefined,
  brief: Brief,
  context: string,
) {
  return (
    !!research &&
    research.stamp === researchStamp(brief) &&
    research.contextStamp === context &&
    ["ready", "empty"].includes(research.status)
  );
}
export function mergeResearch(
  left: PresentationResearch | undefined,
  right: PresentationResearch,
): PresentationResearch {
  if (!left || left.stamp !== right.stamp) return right;
  const sources = left.sources.map((s) => ({ ...s }));
  const evidence = left.evidence.map((e) => ({
    ...e,
    sourceIds: [...e.sourceIds],
  }));
  const mapping = new Map<string, string>();
  for (const source of right.sources) {
    let existing = sources.find((s) => s.url === source.url);
    if (!existing) {
      existing = { ...source, id: `source-${sources.length + 1}` };
      sources.push(existing);
    }
    mapping.set(source.id, existing.id);
  }
  for (const fact of right.evidence) {
    const sourceIds = fact.sourceIds
      .map((id) => mapping.get(id))
      .filter((id): id is string => !!id);
    if (!sourceIds.length) continue;
    const existing = evidence.find((e) => e.text === fact.text);
    if (existing)
      existing.sourceIds = [...new Set([...existing.sourceIds, ...sourceIds])];
    else
      evidence.push({
        id: `evidence-${evidence.length + 1}`,
        text: fact.text,
        sourceIds,
      });
  }
  return {
    ...right,
    sources,
    evidence,
    status: evidence.length ? "ready" : "empty",
    queries: [...new Set([...left.queries, ...right.queries])],
  };
}
export async function searchResearch(brief: Brief): Promise<PresentationResearch> {
  // Public lab has no hosted web-search dependency. The caller must supply
  // source material; do not fabricate citations or fetch arbitrary URLs.
  return { stamp: researchStamp(brief), searchedAt: new Date().toISOString(),
    model: "none", status: "empty", queries: [], sources: [], evidence: [] };
}
