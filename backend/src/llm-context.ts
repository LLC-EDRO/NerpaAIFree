import { createHash } from "node:crypto";
import type { Layout, Outline } from "./domain.js";
import { readingGroups } from './semantic-groups.js';

export const contextVersion = 5;
export type Fact = { id: string; text: string; sourceIds?: string[] };
/** Stable citations for supplied material, not IDs of editable plan slides.
 * Keep exact paragraphs, including units and scope; never infer facts from numbers. */
export function materialFacts(source: string, pool: Fact[] = []): Fact[] {
  const result = [...pool], seen = new Set(pool.map(f => f.text));
  for (const text of source.split(/\n+/u).map(s => s.trim()).filter(Boolean)) {
    if (seen.has(text)) continue;
    seen.add(text);
    result.push({id: `material-${createHash("sha256").update(text).digest("hex").slice(0, 16)}`, text});
  }
  return result;
}

/** Evidence already includes these exact user paragraphs with stable citation IDs.
 * Preserve every other paragraph, including instructions and unselected facts. */
export function unduplicatedSource(source: string, evidence: Fact[]) {
  const included = new Set(evidence.filter(f => f.id.startsWith("material-")).map(f => f.text));
  return source.split(/\n+/u).filter(line => !included.has(line.trim())).join("\n");
}

const words = (text: string) =>
  new Set(
    (
      text
        .toLowerCase()
        .replace(/ё/g, "е")
        .match(/[\p{L}]{4,}/gu) || []
    ).map((w) => (w.length > 6 ? w.slice(0, 6) : w)),
  );
const factValues = (text: string) =>
  (text.match(/\d+(?:[.,]\d+)?/g) || []).filter((n) => !/^20\d\d$/.test(n));

/** Retrieval never rewrites facts. Keep cited facts and plan values, expand on
 * retries, and use the full corpus when lexical evidence is inconclusive. */
export function selectFacts(
  pool: Fact[],
  query: string,
  previous?: any,
  expansion = 0,
): Fact[] {
  if (pool.length <= 10 || expansion >= 2) return pool;
  const cited = new Set<string>(
    Object.values(previous?.fields || {})
      .concat(Object.values(previous?.charts || {}))
      .flatMap((v: any) => v.evidence || []),
  );
  const terms = words(query),
    values = new Set(factValues(query));
  const tokens = pool.map((f) => words(f.text));
  const ranked = pool.map((fact, i) => {
    const overlaps = [...terms].filter((t) => tokens[i].has(t));
    const score = overlaps.reduce(
      (sum, t) =>
        sum + Math.log(1 + pool.length / tokens.filter((s) => s.has(t)).length),
      0,
    );
    return {
      fact,
      score,
      pinned:
        cited.has(fact.id) ||
        cited.has(fact.text) ||
        factValues(fact.text).some((n) => values.has(n)),
    };
  });
  if (ranked.filter((r) => r.score > 0).length < 3) return pool;
  const selected = new Set(
    ranked.filter((r) => r.pinned).map((r) => r.fact.id),
  );
  for (const r of [...ranked]
    .sort((a, b) => b.score - a.score)
    .slice(0, expansion ? 20 : 10))
    selected.add(r.fact.id);
  return pool.filter((f) => selected.has(f.id));
}

export function deckOutline(plan: Outline) {
  return plan.slides.map((s, i) => ({ slide: i + 1, title: s.title }));
}

/** Server-only source copies, XML metadata and sample prose are not a fill prompt. */
export function compactTemplate(
  layout: Layout,
  semantics: any,
  keys?: Set<string>,
) {
  return {
    fields: semantics.fields
      // Explicit groups accompany the same fields below; never infer pairs
      // from numeric shape keys or source XML ordering.
      .filter((f: any) => !keys || keys.has(f.key))
      .map((f: any) => {
        const s = layout.slots.find((s) => s.key === f.key)!;
        const needsGeometryAssistance =
          f.role === "body" &&
          !s.cell &&
          s.text.length > 100 &&
          (s.textFit?.targetChars || s.maxChars) < 30;
        return {
          ...f,
          ...(needsGeometryAssistance ? { needsGeometryAssistance: true } : {}),
          x: s.x,
          y: s.y,
          w: s.w,
          h: s.h,
          font: s.font,
          size: s.size,
          bold: s.bold,
          align: s.align,
          maxChars: s.maxChars,
          ...(!needsGeometryAssistance &&
          ["body", "title", "metric_label", "footer"].includes(f.role) &&
          (s.textFit?.targetChars || s.maxChars)
            ? {
                writingTargetChars:
                  s.textFit?.targetChars ||
                  Math.max(1, Math.floor(s.maxChars * 0.75)),
              }
            : {}),
          ...(s.textFit ? { textFit: s.textFit } : {}),
          ...(s.textRegion ? { textRegion: s.textRegion } : {}),
          ...(s.cell ? { cell: s.cell } : {}),
          ...(f.action === "preserve" ? { originalText: s.text } : {}),
        };
      }),
    readingGroups: readingGroups(semantics,layout.slots),
    charts: layout.charts
      .filter((c: any) => !keys || keys.has(c.key))
      .map((c: any) => ({
        key: c.key,
        type: c.type,
        seriesCount: c.seriesCount,
        pointCount: c.pointCount,
        intent: semantics.charts.find((s: any) => s.key === c.key)?.intent,
      })),
  };
}

export function compactCopy(data: any, pool: Fact[]) {
  if (!data) return undefined;
  const ids = new Map(pool.map((f) => [f.text, f.id]));
  const result = structuredClone(data);
  for (const v of [
    ...Object.values(result.fields || {}),
    ...Object.values(result.charts || {}),
  ] as any[]) {
    v.evidence = [
      ...new Set((v.evidence || []).map((q: string) => ids.get(q) || q)),
    ];
  }
  return result;
}

export function compactHistory(rejected: any[], keys: Set<string>) {
  const relevant = rejected.filter((r) => keys.has(r.key));
  return [...keys].flatMap((key) => {
    const rows = relevant.filter((r) => r.key === key);
    if (!rows.length) return [];
    return [
      {
        key,
        attempts: rows.length,
        reasons: [...new Set(rows.map((r) => r.reason))],
        recent: rows
          .slice(-2)
          .map(({ value, reason, message, details, suggestedMaxChars }) => ({
            value,
            reason,
            message,
            details,
            suggestedMaxChars,
          })),
      },
    ];
  });
}

export function needsRepairImage(issues: any[]) {
  // Images add no information when only a citation or factual claim is wrong.
  return issues.some(
    (i) =>
      i.reason === "overflow" ||
      i.reason === "unverifiable" ||
      /rendered_|occlu|outside|layout|geometry|overlap/.test(JSON.stringify(i)),
  );
}

export function compactIssues(issues: any[], available: Fact[]) {
  const ids = new Set(available.map((f) => f.id));
  return issues.map(({ suggestedEvidence, contextHash, ...issue }) => ({
    ...issue,
    ...(suggestedEvidence
      ? {
          suggestedEvidence: suggestedEvidence.map((s: any) => ({
            number: s.number,
            candidateIds: s.candidates
              .filter((f: Fact) => ids.has(f.id))
              .map((f: Fact) => f.id),
          })),
        }
      : {}),
  }));
}

export function fillContext(input: {
  topic: string;
  userMaterials: string;
  plan: Outline;
  index: number;
  layout: Layout;
  semantics: any;
  dimensions: unknown;
  pool: Fact[];
  frames: any;
  previous?: any;
  issues: any[];
  rejected: any[];
  expansion: number;
  failedRenderAvailable: boolean;
  diagnosis?: unknown;
}) {
  const { layout, semantics, previous, issues, plan, index } = input;
  const keys = issues.length
    ? new Set<string>(issues.map((i) => i.key).filter(Boolean))
    : undefined;
  const query =
    plan.slides[index].title +
    "\n" +
    plan.slides[index].brief +
    "\n" +
    semantics.fields
      .filter((f: any) => !keys || keys.has(f.key))
      .map((f: any) => f.intent)
      .join("\n");
  const researchEvidence = selectFacts(
    input.pool,
    query,
    previous,
    input.expansion,
  );
  const compact = compactCopy(previous, input.pool);
  const allowedPrevious =
    compact && keys
      ? {
          fields: Object.fromEntries(
            Object.entries(compact.fields).filter(([k]) => keys.has(k)),
          ),
          charts: Object.fromEntries(
            Object.entries(compact.charts).filter(([k]) => keys.has(k)),
          ),
        }
      : undefined;
  return {
    topic: input.topic,
    userSource: unduplicatedSource(input.userMaterials, researchEvidence),
    deckOutline: deckOutline(plan),
    currentSlide: index + 1,
    approved: {
      title: plan.slides[index].title,
      brief: plan.slides[index].brief,
    },
    researchEvidence,
    evidenceSelection: {
      available: input.pool.length,
      provided: researchEvidence.length,
      expanded: input.expansion > 0,
    },
    pageDimensions: input.dimensions,
    template: compactTemplate(layout, semantics, keys),
    tableStructure: layout.tableStructure,
    nativeTextStructure: Object.fromEntries(
      layout.slots
        .filter((s) => !keys || keys.has(s.key))
        .map((s) => {
          const paragraphs = (
            input.frames[s.sourceFrameKey || s.key]?.paragraphs || []
          ).filter((p: any) => p.has_text);
          return [
            s.key,
            {
              paragraphCount: paragraphs.length,
              autoNumbered: paragraphs.some((p: any) => p.bullet_auto),
              maxItems:
                paragraphs.length > 0 &&
                paragraphs.every((p: any) => p.bullet_text || p.bullet_auto)
                  ? paragraphs.length
                  : undefined,
              isList:
                paragraphs.length > 0 &&
                paragraphs.every((p: any) => p.bullet_text || p.bullet_auto),
            },
          ];
        }),
    ),
    ...(keys
      ? {
          previous: allowedPrevious,
          // Keep the whole slide visible to the repairer without repeated citations/geometry.
          unchangedContent: {
            fields: Object.fromEntries(
              Object.entries(compact?.fields || {})
                .filter(([k]) => !keys.has(k))
                .map(([k, v]: any) => [k, v.text]),
            ),
            charts: Object.fromEntries(
              Object.entries(compact?.charts || {}).filter(
                ([k]) => !keys.has(k),
              ),
            ),
          },
          issues: compactIssues(issues, researchEvidence),
          rejected: compactHistory(input.rejected, keys),
          previousDiagnosis: input.diagnosis,
          repairContext: {
            allowedFieldKeys: [...keys],
            failedRenderAvailable: input.failedRenderAvailable,
            strategy: issues.some(
              (i) => i.reason === "semantic_context_changed",
            )
              ? "redistribute_within_semantic_group"
              : "targeted_correction",
            repeatedFailures: input.rejected.filter((i) => keys.has(i.key))
              .length,
          },
        }
      : {}),
  };
}

/** Model-only view: native precision, original XML and complete sample text
 * remain in the contract. The image supplies the original visual hierarchy. */
export function descriptionContext(layout: Layout) {
  const box = (s: any) =>
    Object.fromEntries(
      ["x", "y", "w", "h"].map((k) => [k, Math.round(s[k] * 10) / 10]),
    );
  return {
    id: layout.id,
    slots: layout.slots.map((s) => ({
      key: s.key,
      role: s.role,
      text: s.text.slice(0, 240),
      ...box(s),
      size: s.size,
      bold: s.bold,
      align: s.align,
      verticalAlign: s.verticalAlign,
      maxChars: s.maxChars,
      ...(s.cell ? { cell: s.cell, shapeId: s.shapeId } : {}),
      ...(s.groupPath ? { groupPath: s.groupPath } : {}),
      ...(s.textRegion ? { textRegion: s.textRegion } : {}),
      ...(s.textFit
        ? {
            textFit: {
              targetChars: s.textFit.targetChars,
              lines: s.textFit.lines,
            },
          }
        : {}),
    })),
    charts: layout.charts,
  };
}
