import { z } from "zod";
import {tableContentPolicy, maxTableDataRows} from './table-structure.js';
import { templateTextPolicyPrompt } from './template-text-policy.js';
import { groundSemanticsInCards, readingGroups } from './semantic-groups.js';
import { numericReviewIssues } from './numeric-review.js';
import { alignmentSchema, alignmentPrompt, applicableAlignment } from './text-alignment.js';
import { structuralPrefixes, preserveStructuralPrefix, structuralOrdinalMarkers } from "./structural-numbering.js";
import { join } from "node:path";
import { access } from "node:fs/promises";
import { llmJson } from "./llm.js";
import { digest, renderedRepairImage } from "./repair.js";
import { readJson, writeJson } from "./store.js";
import {
  validateCopy,
  copySchema,
  semanticsSchema,
  type Layout,
} from "./domain.js";
import {
  contentReviewPrompt,
  contentReviewVersion,
  parseContentReview,
} from "./content-review.js";
import { compactCopy, selectFacts, materialFacts, type Fact } from "./llm-context.js";
import { HttpError } from "./errors.js";
import { similarAvailableFont } from "./fonts.js";
import { reviewRebuild, identicalRebuildAttempt } from './rebuild-review.js';
import {separateRebuiltFrames,replaceUnsupportedGlyphs} from './measured-rebuild-repair.js';
import {preserveMetricComposition,sourceCompositionPrompt} from './source-composition.js';

export const rebuildVersion = 22;
export const rebuildRenderPolicy = 13;
export const rebuildCopyPolicy = 4;
const box = {
  x: z.number().finite(),
  y: z.number().finite(),
  w: z.number().positive(),
  h: z.number().positive(),
};
const style = {
  font: z.string(),
  size: z.number().min(1).max(144),
  color: z.string().regex(/^#[0-9a-f]{6}$/i),
};
const text = z
  .object({
    ...box,
    ...style,
    key: z.string().regex(/^r_[A-Za-z0-9_]+$/),
    role: z.enum(["text", "step_number"]).optional(),
    bold: z.boolean(),
    align: z.enum(["left", "center", "right"]),
    verticalAlign: z.enum(['top','center','bottom']).optional(),
    alignment: alignmentSchema.optional(),
    rotation: z.literal(0).optional(),
    allowUnderlayShapes: z.array(z.number().int()).max(12).optional(),
  })
  .strict();
const placement = z.object({ ...box, shapeId: z.number().int() }).strict();
const proposalSchema = z
  .object({
    reason: z.string().min(1).max(800),
    texts: z.array(text).min(1).max(80),
    tables: z
      .array(
        z
          .object({
            ...box,
            ...style,
            shapeId: z.number().int(),
            headers: z
              .array(z.string().regex(/^r_[A-Za-z0-9_]+$/))
              .min(1)
              .max(12),
            rows: z
              .array(
                z
                  .array(z.string().regex(/^r_[A-Za-z0-9_]+$/))
                  .min(1)
                  .max(12),
              )
              .min(1)
              .max(maxTableDataRows),
          })
          .strict(),
      )
      .max(8),
    pictures: z.array(placement).max(30),
    charts: z.array(placement).max(12),
    graphicCharts: z.array(z.object({
      groupId:z.number().int(),kind:z.enum(['pie','donut']),holeRatio:z.number().min(0).max(.85),
      bindings:z.array(z.object({shapeId:z.number().int(),fieldKey:z.string()}).strict()).min(2).max(12),
    }).strict()).max(4).default([]),
    data: copySchema,
  })
  .strict();

export function shouldRebuild(issues: any[], rejected: any[]) {
  return [...issues, ...rejected].some((i) =>
    ["overflow", "unverifiable", "layout_unavailable"].includes(i.reason),
  );
}

/** Critical visibility failures get an immediate individual render. Frame
 * margins and decoration bounds are checked in the final batch render instead
 * of launching another converter for every caption. */
export function needsIndividualRepairPreview(issues:any[]) {
  const batchChecked=new Set(['rendered_text_outside_frame','rendered_font_mismatch']);
  return issues.some(i=>Array.isArray(i.details) && i.details.some((d:string)=>
    d.startsWith('rendered_') && !batchChecked.has(d) &&
    !(d==='rendered_text_overlaps_reserved_object' && i.blockerRole==='decoration')));
}
export function rebuildLayout(raw: unknown, original: Layout, profile: any, colorRepairs: Record<string, string> = {}) {
  original={...original,rebuildSemantics:groundSemanticsInCards(original.rebuildSemantics,profile)};
  // Some models reuse native cell addresses. Canonicalize only known cells of
  // this table, preserving their data; do not buy another response for a name.
  const normalized: any = structuredClone(raw);
  for (const table of normalized?.tables || []) {
    if (!Array.isArray(table.headers) || !Array.isArray(table.rows)) continue;
    for (const row of [table.headers, ...table.rows]) {
      if (!Array.isArray(row)) continue;
      row.forEach((key: unknown, i: number) => {
        if (typeof key !== 'string' || key.startsWith('r_')) return;
        if (!original.slots.some(s => s.key === key && s.cell && s.shapeId === table.shapeId)) return;
        const replacement = 'r_' + key;
        const fields = normalized.data?.fields;
        if (!fields || !fields[key] || fields[replacement]) return;
        fields[replacement] = fields[key]; delete fields[key]; row[i] = replacement;
      });
    }
  }
  const p = proposalSchema.parse(normalized);
  for (const table of p.tables) {
    const source=profile.tables.find((t:any)=>t.shapeId===table.shapeId);
    if (!source || table.headers.length<Math.min(2,source.columns) || table.headers.length>source.columns || table.rows.some(row=>row.length!==table.headers.length))
      throw new Error('rebuild_table_grid');
  }
  // A footer may contain a real brand. Once the semantic pass identified it,
  // reconstruction must honor that decision instead of rewriting all anchors.
  const preservedText = new Map<string, string>();
  for (const anchor of profile.textAnchors || []) {
    const semantic = original.rebuildSemantics?.fields?.find((f: any) => f.key === anchor.sourceKey);
    const source = original.slots.find(s => s.key === anchor.sourceKey);
    if (source && semantic?.action === 'preserve' &&
        ['brand', 'decoration', 'page_number'].includes(semantic.role)) {
      preservedText.set(anchor.key, source.text);
      p.data.fields[anchor.key] = {text: source.text, evidence: []};
    }
  }
  const expectedGraphics = profile.vectorCharts || [];
  if (p.graphicCharts.length !== expectedGraphics.length || new Set(p.graphicCharts.map(g=>g.groupId)).size!==p.graphicCharts.length)
    throw new Error('rebuild_vector_chart_mapping_missing');
  for (const graphic of p.graphicCharts) {
    const expected = expectedGraphics.find((g:any)=>g.groupId===graphic.groupId);
    if (!expected) throw new Error('rebuild_vector_chart_unknown');
    const keys = profile.textAnchors.filter((a:any)=>expected.sourceLabels.some((s:any)=>s.key===a.sourceKey)).map((a:any)=>a.key);
    if (graphic.bindings.length!==expected.sectors.length || new Set(graphic.bindings.map(b=>b.shapeId)).size!==expected.sectors.length || graphic.bindings.some(b=>!expected.sectors.some((s:any)=>s.shapeId===b.shapeId)) ||
        new Set(graphic.bindings.map(b=>b.fieldKey)).size!==keys.length || graphic.bindings.some(b=>!keys.includes(b.fieldKey))) throw new Error('rebuild_vector_chart_bindings');
    const values = graphic.bindings.map(b=>{
      const found=[...(p.data.fields[b.fieldKey]?.text || '').matchAll(/(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)\s*%/g)];
      if(found.length!==1)throw new Error('rebuild_vector_chart_percentage');
      return Number(found[0][1].replace(',','.'));
    });
    if(values.some(v=>v<0||v>100)||Math.abs(values.reduce((s,v)=>s+v,0)-100)>.2)throw new Error('rebuild_vector_chart_total');
  }
  for (const item of [...p.texts, ...p.tables]) {
    const preferred = 'shapeId' in item
      ? original.slots.find(s => s.shapeId === item.shapeId)?.font
      : profile.textAnchors?.find((s: any) => s.key === item.key)?.font;
    item.font = similarAvailableFont(item.font, profile.fonts, preferred);
  }
  // Source identities are stable; the model repairs content and geometry, while
  // the server retains typography and native graphic placements.
  if (profile.version >= 2) {
    const anchors: any[] = profile.textAnchors;
    if (p.texts.length !== anchors.length || new Set(p.texts.map(s => s.key)).size !== anchors.length || p.texts.some(s => !anchors.some(a => a.key === s.key)))
      throw new Error("rebuild_keep_source_fields");
    for (const s of p.texts) {
      const a = anchors.find(a => a.key === s.key);
      s.size = Math.max(a.minSize ?? Math.max(Math.min(a.size,12),a.size*.7), Math.min(s.size,a.size));
      Object.assign(s, Object.fromEntries(["font", "color", "bold", "align"].map(k => [k, a[k]])));
      s.verticalAlign = a.verticalAlign || 'top';
      const alignment = applicableAlignment(s.alignment ?? original.textAlignment?.[a.sourceKey],a,p.data.fields[s.key]?.text || '');
      if (alignment) {
        s.alignment = alignment as z.infer<typeof alignmentSchema>;
        if (alignment.horizontal === 'center') s.align = 'center';
        if (alignment.vertical === 'center') s.verticalAlign = 'center';
      } else delete s.alignment;
    }
    // Legacy receipts keep their previous row policy. New profiles let AI
    // choose positions without shifting an unrelated, already valid peer.
    if (profile.version < 5) {
    // Repeated source captions/headings form an aligned row. When one needs
    // more headroom, move its peers with it, preserving the original rhythm.
    const visited = new Set<string>();
    for (const a of anchors) {
      if (visited.has(a.key)) continue;
      const peers = anchors.filter(b => Math.abs(b.y-a.y)<0.2 && Math.abs(b.size-a.size)<0.2 && Math.abs(b.h-a.h)<0.2);
      peers.forEach(b => visited.add(b.key));
      if (peers.length<2) continue;
      const shift = Math.min(...peers.map(b => p.texts.find(t => t.key===b.key)!.y-b.y));
      for (const b of peers) {
        const t = p.texts.find(t => t.key===b.key)!;
        const bottom = Math.max(t.y+t.h,b.y+b.h);
        t.y = b.y+shift;
        t.h = bottom-t.y;
      }
    }
    }
    for (const kind of ["pictures", "charts"] as const)
      for (const s of p[kind]) {
        const a = profile[kind].find((a: any) => a.shapeId === s.shapeId);
        if (a?.box) Object.assign(s, a.box);
      }
  }
  p.texts=preserveMetricComposition(p.texts,profile);
  for (const [key, color] of Object.entries(colorRepairs)) {
    const target = p.texts.find(s => s.key === key);
    if (!target || !profile.colors.includes(color)) throw new Error("rebuild_contrast_color");
    target.color = color;
  }
  const sourcePrefixes = structuralPrefixes(original.slots);
  const sourceMarkers = structuralOrdinalMarkers(original.slots.map(s=>({...s,role:original.rebuildSemantics?.fields?.find((f:any)=>f.key===s.key)?.role || s.role})));
  const sourceMarkerKeys = new Set<string>();
  for (const anchor of profile.textAnchors || []) {
    const prefix=sourcePrefixes.get(anchor.sourceKey);
    const field=p.data.fields[anchor.key];
    const marker=sourceMarkers.get(anchor.sourceKey);
    if (marker !== undefined && field) {
      field.text=marker;
      field.evidence=[];
      sourceMarkerKeys.add(anchor.key);
    }
    if (prefix && field?.text.trim()) {
      field.text=preserveStructuralPrefix(field.text,prefix);
      const target=p.texts.find(t=>t.key===anchor.key);
      if (target) target.role='step_number';
    }
  }
  const tables = p.tables.map(({ headers, rows, ...table }) => {
    const sourceCells=original.slots.filter(s=>s.shapeId===table.shapeId && s.cell);
    const lastRow=Math.max(0,...sourceCells.map(s=>s.cell![0]));
    const allRows=[headers,...rows];
    const cellStyles=Object.fromEntries(allRows.flatMap((row,r)=>row.map((key,c)=>{
      const source=sourceCells.find(s=>s.cell![0]===Math.min(r,lastRow) && s.cell![1]===c);
      return [key,source ? {color:profile.colors.includes(source.color) ? source.color : table.color,font:profile.fonts.includes(source.font) ? source.font : table.font,bold:source.bold ?? r===0,align:source.align || 'left'} : {}];
    })));
    return {...table, rows:allRows, cellStyles};
  });
  const fields: any[] = [...p.texts];
  const ordinalPrefixes = structuralPrefixes(p.texts.map(s => ({key:s.key,role:s.role,text:p.data.fields[s.key]?.text || ""})));
  const standaloneSteps = p.texts
    .filter((s) => s.role === "step_number")
    .map((s) => ({ key: s.key, value: p.data.fields[s.key]?.text || "" }))
    .sort((a, b) => Number(a.value) - Number(b.value));
  const stepKeys = new Set(
    standaloneSteps.length >= 2 &&
      standaloneSteps.length <= 20 &&
      standaloneSteps.every(
        (s, i) => /^\d{1,2}$/.test(s.value) && Number(s.value) === i + 1,
      )
      ? standaloneSteps.map((s) => s.key)
      : [],
  );
  for (const key of sourceMarkerKeys) stepKeys.add(key);
  for (const table of tables)
    for (const [r, row] of table.rows.entries())
      for (const [c, key] of row.entries()) {
        const w = table.w / row.length,
          h = table.h / table.rows.length;
        fields.push({
          ...table,
          key,
          x: table.x + c * w + 5,
          y: table.y + r * h + 4,
          w: w - 10,
          h: h - 8,
          bold: r === 0,
          align: "left",
          ...table.cellStyles[key],
          cell: [r, c],
          shapeId: table.shapeId,
        });
      }
  if (
    !fields.some((s) => s.key === "r_title") ||
    new Set(fields.map((s) => s.key)).size !== fields.length
  )
    throw new Error("rebuild_duplicate_or_missing_title");
  for (const kind of ["pictures", "charts", "tables"] as const) {
    const expected = profile[kind].map((o: any) => o.shapeId);
    if (
      p[kind].length !== expected.length ||
      new Set(p[kind].map((o) => o.shapeId)).size !== expected.length ||
      p[kind].some((o) => !expected.includes(o.shapeId))
    )
      throw new Error("rebuild_must_keep_native_" + kind);
  }
  for (const s of fields)
    if (
      !profile.fonts.includes(s.font) ||
      !profile.colors.includes(s.color) ||
      s.w <= 0 ||
      s.h <= 0
    )
      throw new Error("rebuild_template_style");
  const scene = {
    version: 1,
    fingerprint: profile.fingerprint,
    texts: p.texts,
    ...(Object.keys(colorRepairs).length ? { colorRepairs } : {}),
    tables,
    pictures: p.pictures,
    charts: p.charts,
    graphicCharts: p.graphicCharts,
  };
  const layout: Layout = {
    ...original,
    slots: fields.map((s, i) => ({
      ...s,
      shapeId: s.shapeId ?? 100000 + i,
      text: preservedText.get(s.key) ?? (s.cell?.[0] === 0 || sourceMarkerKeys.has(s.key) ? p.data.fields[s.key]?.text || "" : ""),
      structuralOrdinalPrefix: ordinalPrefixes.get(s.key),
      role: s.key === "r_title" ? "header" : "body",
      maxChars: 4000,
    })),
    tableRows: undefined,
    tableColumns: undefined,
    geometryRepairs: undefined,
  };
  const semantics = semanticsSchema.parse({
    composition: "Восстановленная компоновка",
    fields: layout.slots.map((s) => {
      const anchor = profile.textAnchors?.find((a:any)=>a.key===s.key);
      const semantic = original.rebuildSemantics?.fields?.find((f:any)=>f.key===anchor?.sourceKey);
      return ({
      key: s.key,
      role:
        s.key === "r_title"
          ? "title"
          : stepKeys.has(s.key) || ordinalPrefixes.has(s.key)
            ? "step_number"
            : semantic?.role || "body",
      group: s.cell ? `table_${s.shapeId}_${s.cell[0]}` : semantic?.group || s.key,
      intent: semantic?.intent || s.key,
      required: true,
      action: preservedText.has(s.key) ? "preserve" : "replace",
    });}),
    charts: original.charts.map((c) => ({
      key: c.key,
      intent: "Данные исходной диаграммы по новой теме",
    })),
  });
  return { proposal: p, scene, layout, semantics };
}

export function proposalFromRebuildAttempt(previous: any) {
  return {
    reason: previous.reason,
    texts: structuredClone(previous.scene.texts),
    tables: previous.scene.tables.map(({ rows, cellStyles, ...t }: any) => ({
      ...t,
      headers: rows[0],
      rows: rows.slice(1),
    })),
    pictures: previous.scene.pictures,
    charts: previous.scene.charts,
    graphicCharts: previous.scene.graphicCharts || [],
    data: structuredClone(previous.data),
  };
}

export function useLocalRebuildPatch(attempt: number, previous: any) {
  // One new composition after local patches; do not lock the AI in the same grid.
  return (
    attempt !== 4 &&
    !!previous?.issues?.length &&
    previous.issues.every(
      (i: any) =>
        i.key &&
        ["overflow", "unverifiable"].includes(i.reason) &&
        previous.scene?.texts.some((s: any) => s.key === i.key),
    )
  );
}

export function patchRebuiltText(
  raw: unknown,
  previous: any,
  allowed: Set<string>,
  layout: Layout,
  profile: any,
) {
  const patch = z
    .object({
      reason: z.string().min(1).max(800),
      changes: z
        .array(
          z
            .object({
              key: z.string(),
              ...box,
              size: z.number().min(1).max(144).optional(),
              allowUnderlayShapes: z.array(z.number().int()).max(12).optional(),
              text: z.string().max(4000).optional(),
              evidence: z.array(z.string()).optional(),
            })
            .strict(),
        )
        .min(1)
        .max(24),
    })
    .strict()
    .parse(raw);
  if (
    new Set(patch.changes.map((c) => c.key)).size !== patch.changes.length ||
    patch.changes.some((c) => !allowed.has(c.key))
  )
    throw new Error("rebuild_patch_keys");
  const proposal = proposalFromRebuildAttempt(previous);
  const prefixes=structuralPrefixes(previous.scene.texts.map((s:any)=>({key:s.key,role:s.role,text:previous.data.fields[s.key]?.text || ''})));
  for (const { key, text, evidence, ...geometry } of patch.changes) {
    Object.assign(
      proposal.texts.find((s: any) => s.key === key),
      geometry,
    );
    if (text !== undefined)
      proposal.data.fields[key] = {
        text: prefixes.has(key) ? preserveStructuralPrefix(text,prefixes.get(key)!) : text,
        evidence: evidence || proposal.data.fields[key].evidence,
      };
  }
  return rebuildLayout(proposal, layout, profile, previous.scene.colorRepairs);
}

export function rebuildAttemptLimit(count: number, policyReplay: boolean) {
  return policyReplay ? Math.max(6, count + 1) : 6;
}

/** One measured adjustment after AI geometry repair; no text loss or paid rewrite. */
export function fittedRebuildSizes(scene: any, issues: any[], profile: any) {
  const texts = structuredClone(scene.texts);
  let changed = false;
  for (const text of texts) {
    const issue = issues.find(i => i.key === text.key && i.reason === 'overflow' && i.details?.includes('source_text_frame_overflow'));
    const anchor = profile.textAnchors?.find((a: any) => a.key === text.key);
    if (!issue || !anchor) continue;
    const ratios = [['measuredHeightPt','availableHeightPt'],['measuredWidthPt','availableWidthPt']]
      .map(([measured,available]) => issue[measured] > 0 && issue[available] > 0 ? issue[available]/issue[measured] : 1);
    const size = Math.max(anchor.minSize ?? Math.max(Math.min(anchor.size,12),anchor.size*.7), Math.floor(text.size*Math.min(1,...ratios)*.96*10)/10);
    if (size < text.size-.1) { text.size = size; changed = true; }
  }
  return changed ? {...scene,texts:preserveMetricComposition(texts,profile)} : undefined;
}

export function contrastFallback(warnings: any[], palette: string[]) {
  const luminance=(hex:string)=>hex.slice(1).match(/../g)!.map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
  return warnings.flatMap(w=>{
    if(w.reason!=="rendered_text_low_contrast" || !(typeof w.backgroundCoverage === "number" && w.backgroundCoverage>=.85) || !/^#[0-9a-f]{6}$/i.test(w.backgroundColor || "")) return [];
    const background=luminance(w.backgroundColor);
    const ranked=palette.filter(c=>/^#[0-9a-f]{6}$/i.test(c)).map(color=>{const ink=luminance(color);return {color,ratio:(Math.max(ink,background)+.05)/(Math.min(ink,background)+.05)};}).sort((a,b)=>b.ratio-a.ratio);
    return ranked[0]?.ratio>=4.5 ? [{key:w.key,color:ranked[0].color}] : [];
  });
}

export function patchRebuiltContrast(raw: unknown, previous: ReturnType<typeof rebuildLayout>, allowed: Set<string>, layout: Layout, profile: any) {
  const patch = z.object({reason:z.string().min(1).max(800),changes:z.array(z.object({key:z.string(),color:z.string().regex(/^#[0-9a-f]{6}$/i)}).strict()).max(24)}).strict().parse(raw);
  if (new Set(patch.changes.map(c=>c.key)).size !== patch.changes.length || patch.changes.some(c=>!allowed.has(c.key) || !profile.colors.includes(c.color))) throw new Error("rebuild_contrast_keys_or_palette");
  const proposal = proposalFromRebuildAttempt({reason:patch.reason,scene:previous.scene,data:previous.proposal.data});
  return rebuildLayout(proposal,layout,profile,{...previous.scene.colorRepairs,...Object.fromEntries(patch.changes.map(c=>[c.key,c.color]))});
}

export async function rebuildSlide(input: {
  folder: string;
  output: string;
  assembled: string;
  index: number;
  sha: string;
  layout: Layout;
  approved: any;
  topic: string;
  source: string;
  pool: Fact[];
  previous: any;
  issues: any[];
  rejected: any[];
  profile?: any;
  blankImage?: string;
  deferRender?: boolean;
  signal: AbortSignal;
  native: (
    action: string,
    folder: string,
    extra: any,
    signal?: AbortSignal,
  ) => Promise<any>;
}) {
  const { index, folder, output, assembled, signal } = input;
  const profile = input.profile || (
    await input.native("rebuild_context", assembled, {}, signal)
  )[input.layout.id];
  if (!profile)
    throw new HttpError(
      422,
      "Недоступно описание дизайна для восстановления",
      "rebuild_profile_missing",
    );
  const fingerprint = digest({
    version: rebuildVersion,
    profile: profile.fingerprint,
    review: contentReviewVersion,
    sha: input.sha,
    source: input.source,
    approved: {title: input.approved.title, brief: input.approved.brief},
    layout: input.layout.id,
  });
  const pool = materialFacts(input.source, input.pool);
  const statePath = join(output, `rebuild-${index}.json`);
  let state: any = { fingerprint, attempts: [] };
  try {
    const saved = await readJson(statePath);
    if (saved.fingerprint === fingerprint) state = saved;
    else
      await writeJson(
        join(output, `rebuild-${index}-history-${saved.fingerprint}.json`),
        saved,
      );
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
  }
  const priorImage = await renderedRepairImage(output, index, input.previous);
  // Recheck the saved candidate under relaxed render policy before buying a
  // new model response. Keep all prior attempts and already completed slides.
  const renderReplay =
    (state.renderPolicy !== rebuildRenderPolicy || state.attempts.at(-1)?.warnings?.some((w: any) => w.reason === "contrast_repair_unavailable")) &&
    state.attempts
      .at(-1)
      ?.issues?.every(
        (i: any) =>
          i.details?.length &&
          i.details.every((d: string) => ["rendered_text_outside_frame", "rendered_text_indistinguishable"].includes(d)),
      )
      ? state.attempts.at(-1)
      : undefined;
  const last = state.attempts.at(-1);
  let copyReplay = false;
  if (state.copyPolicy !== rebuildCopyPolicy && last?.issues?.length && last.issues.every((i:any)=>["unsupported_number","overflow","unverifiable"].includes(i.reason))) {
    const candidate = rebuildLayout(proposalFromRebuildAttempt(last), input.layout, profile, last.scene.colorRepairs);
    // Replay only when the CURRENT validator independently accepts every value.
    copyReplay = !validateCopy(candidate.proposal.data, candidate.layout, candidate.semantics, input.source, pool).issues.length;
  }
  let replay = renderReplay || (copyReplay ? last : undefined);
  if (replay?.warnings?.some((w: any) => w.reason === "contrast_repair_unavailable")) state.contrastRepairAttempted = false;
  const limit = Math.max(state.attemptLimit || 6, rebuildAttemptLimit(state.attempts.length, !!replay) + (copyReplay ? 2 : 0));
  state.attemptLimit = limit;
  const runLimit = Math.min(limit, state.attempts.length + 2);
  for (let attempt = state.attempts.length; attempt < runLimit; attempt++) {
    signal.throwIfAborted();
    const previous = state.attempts.at(-1);
    const patchable = useLocalRebuildPatch(attempt, previous);
    let repairImage = priorImage;
    if (
      previous?.issues?.some(
        (i: any) =>
          Array.isArray(i.details) &&
          i.details.some((d: string) => d.startsWith("rendered_")),
      )
    ) {
      const file = join(output, `rebuild-preview-${index}`, "slide-0.png");
      if (
        await access(file).then(
          () => true,
          () => false,
        )
      )
        repairImage = file;
    }
    const skipContentReview = !!replay && !copyReplay;
    let proposed = replay
      ? rebuildLayout(proposalFromRebuildAttempt(replay), input.layout, profile, replay.scene.colorRepairs)
      : patchable
        ? await llmJson({
            folder,
            signal,
            stage: `fill-rebuild-${index + 1}-${attempt}`,
            reasoning: attempt === 0 ? "low" : "medium",
            images: [
              join(assembled, "previews", `slide-${index}.png`),
              ...(input.blankImage ? [input.blankImage] : []),
              ...(repairImage ? [repairImage] : []),
            ],
            prompt: sourceCompositionPrompt + "\n" +
              "Исправь ТОЛЬКО ошибочные текстовые рамки уже созданной компоновки. Остальные тексты и объекты сохранит сервер. Ответ {reason,changes:[{key,x,y,w,h,size?,allowUnderlayShapes?,text?,evidence?}]}. Геометрия в pt. При переполнении сохрани готовую мысль и сначала увеличь рамку в свободное место; measuredHeightPt и measuredWidthPt — измеренный размер текста; сравни оба с availableHeightPt/availableWidthPt, исправляй именно переполненную ось и добавь 10% запаса. Увеличение высоты не исправляет слишком длинное неразрывное слово. Если места нет, кратко перефразируй именно этот текст. Номер шага в начале заголовка сохраняется сервером: сокращай только содержательную часть после номера, оставляя место и для самого номера. Сохрани привязку к исходному полю из profile.textAnchors: новая рамка остаётся внутри container/repairRegion, сохраняет смысловую колонку или сторону шкалы, но не обязана пересекать сломанную исходную рамку. Если расширение невозможно, можно уменьшить size до minSize соответствующего textAnchor; не меняй семейство шрифта. Не пересекай соседние объекты и protectedBoxes. Если blockerShapeId — фон либо пустой контур позади текста, разреши его в allowUnderlayShapes, выбирая только из underlayCandidates этого поля. Это правило не разрешает перекрывать фотографии, графики, логотипы и передние объекты. Не повторяй неудачную геометрию и отклонённые тексты. overflowPt и renderedTextBox показывают фактический выход в PDF; используй эти размеры с запасом, не ограничивайся suggestedMaxChars. Пиши на языке темы; не включай инструкции по заполнению в текст. Если текст уже хороший, text и evidence не передавай. Первое изображение — исходный заполненный образец, второе — чистая основа с сохранённым дизайном, третье при наличии — неудачный рендер. allowedKeys обязателен.",
            payload: {
              profile,
              imageRoles: ['source_example', ...(input.blankImage ? ['blank_design'] : []), ...(repairImage ? ['failed_render'] : [])],
              allowedKeys: previous.issues.map((i: any) => i.key),
              issues: previous.issues,
              rejectedPatches: state.attempts
                .slice(-3,-1)
                .map((a: any) => ({
                  issues: a.issues,
                  texts: a.scene.texts.filter((t: any) =>
                    previous.issues.some((i: any) => i.key === t.key),
                  ),
                  fields: compactCopy(a.data, pool),
                })),
              scene: previous.scene,
              fields: compactCopy(previous.data, pool),
              approved: {title: input.approved.title, brief: input.approved.brief},
              researchEvidence: selectFacts(
                pool,
                input.approved.brief,
                previous.data,
              ),
            },
            validate: (raw) =>
              patchRebuiltText(
                raw,
                previous,
                new Set(previous.issues.map((i: any) => i.key)),
                input.layout,
                profile,
              ),
          })
        : await llmJson({
            folder,
            signal,
            stage: `fill-rebuild-${index + 1}-${attempt}`,
            // The first constrained proposal already has measured repair
            // regions and source examples. Escalate reasoning only after a
            // failed proposal; keep the same physical/content checks.
            reasoning: attempt === 0 ? "low" : "medium",
            images: [
              join(assembled, "previews", `slide-${index}.png`),
              ...(input.blankImage ? [input.blankImage] : []),
              ...(priorImage ? [priorImage] : []),
            ],
            prompt: sourceCompositionPrompt + '\n' + tableContentPolicy + '\n' + templateTextPolicyPrompt + '\n' + alignmentPrompt + '\n' + `Исправь сломанные поля исходного слайда, сохранив его КОМПОЗИЦИЮ, а не только палитру. Порядок изображений указан в imageRoles. Если чистая основа недоступна, работай по исходному образцу и структуре. Первое изображение — заполненный образец дизайна; второе — тот же слайд с очищенными редактируемыми текстами. Третье при наличии — неудачный результат. Сначала сравни их: отдели декоративный фон от реальных препятствий, найди доступное место. Дай JSON как проверяемые правила размещения каждого блока: координаты, размеры, кегль, выравнивание и готовый текст. Все существенные числа, даты, время и предметы измерения из approved распределяй ПО СЛАЙДУ целиком: один факт может занимать несколько связанных полей. Не теряй факт ради сокращения, не требуй всех фактов в каждой подписи. Проверяй смысл вместе с геометрией, не используй вместимость сломанного исходного поля как норму. Сервер копирует весь исходный слайд: линии, шкалы, точки, карточки, стрелки, группы, иллюстрации, логотипы и их порядок слоёв остаются на месте. Пиши на языке темы и материалов. Служебные инструкции о заполнении, проверке и наличии чисел не включай в видимый текст. Нельзя заменять временную шкалу, схему или карточки обычными абзацами. profile.textAnchors перечисляет ВСЕ исходные текстовые поля с ключами, стилями и позициями. Верни ровно эти ключи: не удаляй, не объединяй поля и не создавай новые. sourceSemantics задаёт связи полей: сохраняй группу метрика + единица/предмет + подпись. Крупное число не может остаться без объяснения, что оно измеряет. Не заменяй название показателя условиями или общим текстом; для них есть отдельные поля. Сохрани распределение смысла по исходным колонкам/этапам и подписи над/под соответствующими объектами. Перепиши текст по approved под вместимость каждого поля. Обязательно сразу исправь слишком низкие исходные рамки: под одну строку нужно примерно 1.35 размера шрифта, а под несколько — больше. Если рамку нельзя увеличить вниз, сдвинь её немного вверх. У повторяющихся заголовков/подписей одной строки сохраняй одинаковую высоту расположения (предлагай согласованные позиции и размеры сам, учитывая препятствия). При необходимости увеличь рамку в ближайшее свободное место, сдвинув её минимально; новая рамка остаётся внутри container/repairRegion; пересечение со сломанной исходной рамкой не обязательно. Не заходи на соседние тексты, за страницу или за содержащую поле карточку. Декор — не всегда препятствие: сравни чистую основу и заполненный образец. Если underlayCandidates содержит фон карточки или пустой контур ПОЗАДИ этого текста, можешь явно вернуть allowUnderlayShapes:[ID]. Только такие подтверждённые фоновые фигуры можно считать подложкой. Передние объекты, фотографии, диаграммы и логотипы остаются препятствиями. Семейство шрифта и цвет каждого поля сохраняются из textAnchors. По умолчанию сохраняются align и verticalAlign; обоснованную центровку можно предложить через alignment. В высокой рамке для отдельного короткого заголовка сохраняй высоту и центр исходного блока: не сжимай такую рамку до высоты строки, иначе центровка перестанет соответствовать фигуре. Размер сначала сохраняй; если рамку нельзя расширить, допускается уменьшение до minSize из textAnchor. Для соседних равноценных подписей выбирай согласованный размер. Не увеличивай исходные маленькие подписи. Если исходное поле случайно повёрнуто и мешает чтению, явно передай rotation:0, чтобы выпрямить именно его; осмысленные наклонные элементы сохрани. Каждый picture/chart сохраняет исходную геометрию. Таблицы остаются редактируемыми таблицами; можно уменьшить число колонок до осмысленных свойств, одна запись на строку. reason — понятное описание исправления. Не добавляй заметки о восстановлении внутрь слайда. Геометрия в pt. Первая попытка: адаптируй именно исходную структуру. Повторная: исправь причину из failedAttempts, не меняй тип композиции и не повторяй тесные рамки.
Ответ {reason:"что исправлено",texts:[{key:"r_title",x,y,w,h,font,size,color,bold,align:"left"}],tables:[{shapeId,x,y,w,h,font,size,color,headers:["r_header1","r_header2"],rows:[["r_cell1","r_cell2"]]}],pictures:[{shapeId,x,y,w,h}],charts:[{shapeId,x,y,w,h}],data:{fields:{r_title:{text:"...",evidence:[]},r_cell1:{text:"...",evidence:["ID или точная цитата"]}},charts:{исходный_ключ:{title,categories,series:[{name,values}],evidence:[]}}}}. headers содержит ключи отдельных названий колонок, rows — только строки данных; строка данных не заменяет заголовки колонок. Каждый ключ texts, headers и rows должен встречаться в data.fields ровно один раз. data.charts использует исходные ключи, число рядов и категорий nativeCharts. Пустые массивы для отсутствующих типов. Значения fields — готовый текст по теме, исходные тексты шаблона не являются фактами. Числа подтверждаются evidence из переданных материалов: используй переданный ID material-* или evidence-*, а не сокращённую цитату с многоточием или префиксом userSource. Можно перефразировать источник без потери смысла. Если profile.vectorCharts не пуст, это диаграмма из векторных фигур: одних новых подписей недостаточно. Верни graphicCharts:[{groupId,kind:"donut" или "pie",holeRatio:доля внутреннего радиуса,bindings:[{shapeId,fieldKey}]}] для КАЖДОГО кандидата. По исходному изображению, цветам sectors и sourceLabels свяжи каждый сектор с новым полем r_...; bindings идут по часовой стрелке от верхней точки. Сохрани тип и толщину кольца исходного образца. Каждая подпись должна содержать ровно одну новую долю с %, сумма долей 100. Геометрию секторов пересчитает сервер по этим подписям и добавит слева от каждой подписи цветной маркер легенды. Оставь 10 pt свободного места слева от подписи. Содержательное имя категории вместе с процентом обязательно; размещай легенду читаемо вокруг графика, не поверх него. Старые проценты не являются фактами. Если vectorCharts пуст, graphicCharts:[]. Не возвращай исполняемый код. Для последовательных числовых маркеров используй role:"step_number".`,
            payload: {
              profile,
              imageRoles: ['source_example', ...(input.blankImage ? ['blank_design'] : []), ...(priorImage ? ['failed_render'] : [])],
              approved: {title: input.approved.title, brief: input.approved.brief},
              requestedAlignment: input.layout.textAlignment,
              sourceSemantics: groundSemanticsInCards(input.layout.rebuildSemantics,profile),
              topic: input.topic,
              researchEvidence: selectFacts(
                pool,
                input.approved.title + " " + input.approved.brief,
                input.previous,
              ),
              nativeCharts: input.layout.charts,
              previous: compactCopy(input.previous, pool),
              rootIssues: input.issues,
              failedAttempts: state.attempts.slice(-1).map((a: any) => ({
                reason: a.reason, issues: a.issues,
                texts: a.scene.texts.filter((t:any)=>a.issues.some((i:any)=>!i.key || i.key===t.key)),
                data: compactCopy(a.data, pool),
              })),
            },
            validate: (raw) => rebuildLayout(raw, input.layout, profile),
          });
    replay = undefined;
    const checked = validateCopy(
      proposed.proposal.data,
      proposed.layout,
      proposed.semantics,
      input.source,
      pool,
    );
    const numericReview=numericReviewIssues(checked.issues);
    let issues: any[] = numericReview.issues;
    // No repeat QA, render or second full composition for an unchanged answer.
    // The caller keeps the best filled candidate and exposes its warnings.
    if (!skipContentReview && identicalRebuildAttempt(state.attempts, proposed.scene, checked.data)) {
      state.stopReason = 'unchanged_repair';
      await writeJson(statePath, state);
      break;
    }
    const slide = {
      title: input.approved.title,
      native: {
        sourceSlideId: input.layout.id,
        mode: "rebuild",
        preserveTemplate: true,
        ordinal: index + 1,
        rebuild: proposed.scene,
        fields: Object.fromEntries(
          Object.entries(checked.data.fields).map(([k, v]) => [k, v.text]),
        ),
        charts: Object.fromEntries(
          Object.entries(checked.data.charts).map(([k, { evidence, ...v }]) => [
            k,
            v,
          ]),
        ),
      },
    };
    if (!issues.length) {
      try {
        issues = (
          await input.native("fit", assembled, { slides: [slide] }, signal)
        ).issues;
        const moved = separateRebuiltFrames(slide.native.rebuild,issues,profile);
        const adjusted = fittedRebuildSizes(moved||slide.native.rebuild, issues, profile)||moved;
        const glyphs = replaceUnsupportedGlyphs(checked.data,issues,new Set(proposed.semantics.fields.filter(f=>f.action==='preserve').map(f=>f.key)));
        if (adjusted||glyphs) {
          const nextScene=adjusted||slide.native.rebuild;
          const nextFields=glyphs?Object.fromEntries(Object.entries<any>(glyphs.fields).map(([k,v])=>[k,v.text])):slide.native.fields;
          const candidate = {...slide,native:{...slide.native,rebuild:nextScene,fields:nextFields}};
          const fit = await input.native("fit", assembled, {slides:[candidate]}, signal);
          const signature=(i:any)=>JSON.stringify([i.key,i.reason,i.details]);
          const previousIssues=new Set(issues.map(signature));
          if (fit.issues.length < issues.length && fit.issues.every((i:any)=>previousIssues.has(signature(i)))) {
            slide.native.rebuild = nextScene;
            slide.native.fields = nextFields;
            proposed.scene = nextScene;
            if(glyphs){checked.data=glyphs;proposed.proposal.data=glyphs;}
            for (const text of nextScene.texts) {
              const placement={x:text.x,y:text.y,w:text.w,h:text.h,size:text.size};
              Object.assign(proposed.layout.slots.find(s=>s.key===text.key)!,placement);
              Object.assign(proposed.proposal.texts.find(s=>s.key===text.key)!,placement);
            }
            issues = fit.issues;
          }
        }
      } catch (e) {
        if (!(e instanceof HttpError)) throw e;
        issues = [{ reason: e.code, message: e.message, details: e.details }];
      }
    }
    let renderWarnings: any[] = [...numericReview.warnings];
    if (!issues.length && (!skipContentReview || numericReview.warnings.length)) {
      const review = await reviewRebuild(state, {
        folder,
        signal,
        stage: `content-review-rebuild-${index + 1}-${attempt}`,
        prompt: contentReviewPrompt,
        payload: {
          topic: input.topic,
          userSource: input.source,
          approved: {title: input.approved.title, brief: input.approved.brief},
          data: compactCopy(checked.data, pool),
          semantics: proposed.semantics,
          readingGroups: readingGroups(proposed.semantics,proposed.layout.slots,checked.data),
          numericWarnings: numericReview.warnings.map(({key,text,unsupportedNumbers})=>({key,text,unsupportedNumbers})),
          tableCells: proposed.layout.slots
            .filter((s) => s.cell)
            .map((s) => ({ key: s.key, table: s.shapeId, cell: s.cell })),
        },
        validate: (raw) => parseContentReview(raw, checked.data, input.source, proposed.semantics.fields),
      });
      issues = review.issues;
      if (review.unavailable) renderWarnings.push({reason:'content_review_unavailable',message:'Проверка содержания недоступна. Сохранён заполненный слайд; проверьте подписи и показатели.'});
    }
    if (!issues.length && !input.deferRender) {
      try {
        const preview = await input.native(
          "preview",
          assembled,
          { slides: [slide], output: join(output, `rebuild-preview-${index}`) },
          signal,
        );
        issues = preview.issues;
        renderWarnings.push(...(preview.warnings || []));
        const keys = new Set<string>(renderWarnings.filter(w => w.reason === "rendered_text_low_contrast" && proposed.scene.texts.some(t => t.key === w.key)).map(w => w.key));
        if (!issues.length && keys.size && !state.contrastRepairAttempted) {
          // Optional visual polish gets one bounded pass, never six rewrites.
          state.contrastRepairAttempted = true;
          await writeJson(statePath, state);
          const originalScene = slide.native.rebuild;
          try {
            const candidate = await llmJson({
              folder, signal, stage: `fill-contrast-${index + 1}`, reasoning: "low",
              images: [join(output, `rebuild-preview-${index}`, "slide-0.png")],
              prompt: "Проверь читаемость отмеченного текста на изображении. Проверка контраста эвристическая. Если текст читается нормально, верни changes:[]. Иначе выбери контрастный цвет из palette, подходящий к реальному фону и стилю. Меняй ТОЛЬКО цвет allowedKeys; не переписывай текст, не меняй рамки или композицию. Ответ {reason,changes:[{key,color}]}. Не используй светлый неон на белом фоне или тёмный текст на тёмном фоне.",
              payload: { allowedKeys: [...keys], palette: profile.colors, fields: proposed.scene.texts.filter(t => keys.has(t.key)), warnings: renderWarnings },
              validate: raw => patchRebuiltContrast(raw, proposed, keys, input.layout, profile),
            });
            slide.native.rebuild = candidate.scene;
            const recheck = await input.native("preview", assembled, {slides:[slide],output:join(output,`rebuild-preview-${index}`)}, signal);
            if (!recheck.issues.length) {
              proposed = candidate;
              renderWarnings = [...renderWarnings.filter(w=>w.reason==='content_review_unavailable'),...(recheck.warnings || [])];
            } else {
              slide.native.rebuild = originalScene;
              renderWarnings.push({reason:"contrast_repair_skipped",details:recheck.issues});
            }
          } catch (error) {
            signal.throwIfAborted();
            slide.native.rebuild = originalScene;
            const unavailable = {reason:"contrast_repair_unavailable",code:error instanceof HttpError ? error.code : (error as NodeJS.ErrnoException)?.code || "contrast_repair_error",details:error instanceof HttpError ? error.details : undefined};
            state.contrastProviderFailure = unavailable;
            const changes = contrastFallback(renderWarnings, profile.colors).filter(c=>keys.has(c.key));
            if (changes.length) {
              const candidate = patchRebuiltContrast({reason:"Повышена читаемость текста цветом из палитры шаблона на проверенном однотонном фоне",changes}, proposed, keys, input.layout, profile);
              slide.native.rebuild = candidate.scene;
              try {
                const recheck = await input.native("preview", assembled, {slides:[slide],output:join(output,`rebuild-preview-${index}`)}, signal);
                if (!recheck.issues.length) { proposed=candidate;renderWarnings=[...renderWarnings.filter(w=>w.reason==='content_review_unavailable'),...(recheck.warnings || [])]; }
                else { slide.native.rebuild=originalScene;renderWarnings.push(unavailable); }
              } catch {
                signal.throwIfAborted();slide.native.rebuild=originalScene;renderWarnings.push(unavailable);
              }
            } else renderWarnings.push(unavailable);
          }
        }
      } catch (e) {
        if (!(e instanceof HttpError)) throw e;
        issues = [{ reason: e.code, message: e.message, details: e.details }];
      }
    }
    state.attempts.push({
      reason: proposed.proposal.reason,
      scene: proposed.scene,
      data: checked.data,
      issues,
      warnings: renderWarnings,
    });
    state.renderPolicy = rebuildRenderPolicy;
    state.copyPolicy = rebuildCopyPolicy;
    await writeJson(statePath, state);
    if (!issues.length)
      return {
        slide,
        data: checked.data,
        notice: {
          slide: index + 1,
          reason: proposed.proposal.reason,
          warnings: renderWarnings,
          originalIssues: input.issues.map((i) => ({
            key: i.key,
            reason: i.reason,
            details: i.details,
          })),
        },
        fingerprint,
      };
  }
  throw new HttpError(
    422,
    "Не удалось безопасно восстановить слайд в стиле шаблона",
    "slide_rebuild_failed",
    {
      slide: index + 1,
      attempts: state.attempts.length,
      issues: state.attempts.at(-1)?.issues,
    },
  );
}
