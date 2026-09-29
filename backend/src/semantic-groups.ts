import { join } from "node:path";
import { z } from "zod";
import { llmJson } from "./llm.js";
import { digest } from "./repair.js";
import { readJson, writeJson } from "./store.js";

/** Recover only isolated, mutually nearest number/caption pairs with matching
 * column alignment. Shape IDs and XML order are not reading order. Dense or
 * ambiguous arrangements, tables and groups with several captions stay AI-led. */
export function groundMetricPairs(semantics:any, slots:any[]) {
  if (!semantics?.fields) return semantics;
  const eligible=semantics.fields.filter((f:any)=>f.action==='replace' &&
    ['metric','metric_label','unit','body'].includes(f.role) &&
    semantics.fields.filter((g:any)=>g.group===f.group).length<=2)
    .map((f:any)=>({field:f,box:slots.find(s=>s.key===f.key)}))
    .filter((p:any)=>p.box && !p.box.cell && ['x','y','w','h'].every(k=>Number.isFinite(p.box[k])) && p.box.w>0 && p.box.h>0);
  const metrics=eligible.filter((p:any)=>p.field.role==='metric');
  const labels=eligible.filter((p:any)=>p.field.role!=='metric');
  const distance=(a:any,b:any)=>{
    const left=Math.abs(a.x-b.x),center=Math.abs(a.x+a.w/2-b.x-b.w/2);
    if (Math.min(left,center)>Math.max(2,Math.min(a.w,b.w)*.06)) return Infinity;
    const gap=Math.max(a.y-b.y-b.h,b.y-a.y-a.h);
    return gap>=0 && gap<=Math.max(a.h,b.h)*1.5 ? gap : Infinity;
  };
  const nearest=(item:any,peers:any[])=>{
    const ranked=peers.map(p=>({p,d:distance(item.box,p.box)})).sort((a,b)=>a.d-b.d);
    return Number.isFinite(ranked[0]?.d) && (!Number.isFinite(ranked[1]?.d) || ranked[1].d-ranked[0].d>Math.max(4,ranked[0].d*.35)) ? ranked[0].p : undefined;
  };
  const result=structuredClone(semantics);
  for (const metric of metrics) {
    const label=nearest(metric,labels);
    if (!label || nearest(label,metrics)!==metric) continue;
    const group=`visual_pair_${metric.field.key}`;
    for (const key of [metric.field.key,label.field.key]) result.fields.find((f:any)=>f.key===key).group=group;
  }
  return result;
}

/** Give the model explicit reading units instead of asking it to reconstruct
 * them from a flat map whose key order can cross cards or timeline events. */
export function readingGroups(semantics:any, slots:any[], data?:any) {
  return [...new Set<string>(semantics.fields.filter((f:any)=>f.action==='replace').map((f:any)=>f.group))].map(group=>({
    group,fields:semantics.fields.filter((f:any)=>f.action==='replace' && f.group===group)
      .sort((a:any,b:any)=>{
        const x=slots.find(s=>s.key===a.key),y=slots.find(s=>s.key===b.key);
        return (x?.y||0)-(y?.y||0) || (x?.x||0)-(y?.x||0);
      }).map((f:any)=>({key:f.key,role:f.role,...(data ? {text:data.fields[f.key]?.text} : {})})),
  }));
}

/** Small native cards are stronger evidence of ownership than an AI's field
 * order. Do not merge full-slide panels, tables, branding or dense diagrams. */
export function groundSemanticsInCards(semantics:any, profile:any) {
  if (!semantics?.fields || !profile?.page || !(profile.page.w>0 && profile.page.h>0)) return semantics;
  const groups=new Map<number,any[]>();
  for(const anchor of profile.textAnchors || []) {
    const card=anchor.container;
    const field=semantics.fields.find((f:any)=>f.key===anchor.sourceKey);
    if (!card || !Number.isInteger(card.shapeId) || !(card.w>0 && card.h>0) || !field || field.action!=='replace' ||
      ['brand','page_number'].includes(field.role) || card.w*card.h > profile.page.w*profile.page.h*.45) continue;
    const members=groups.get(card.shapeId) || [];
    members.push(field);groups.set(card.shapeId,members);
  }
  const result=structuredClone(semantics);
  for(const [id,members] of groups) {
    if(members.length<2 || members.length>4)continue;
    for(const member of members)result.fields.find((f:any)=>f.key===member.key).group=`native_card_${id}`;
  }
  return result;
}

export function needsGroupRepair(fields: any[]) {
  return fields.some(f => f.action === "replace" && ["metric_label", "unit", "body"].includes(f.role)) &&
    fields.some(f => f.role === "metric" && f.action === "replace" && fields.filter(g => g.group === f.group).length === 1);
}

export function applySemanticGroups(raw: unknown, semantics: any) {
  const { groups } = z.object({groups: z.array(z.array(z.string()).min(1)).max(200)}).strict().parse(raw);
  const keys = groups.flat(), expected = semantics.fields.filter((f: any) => f.action === "replace").map((f: any) => f.key);
  if (keys.length !== expected.length || new Set(keys).size !== keys.length || keys.some(k => !expected.includes(k)))
    throw new Error("Group mapping must contain every replaceable field exactly once; protected fields cannot change");
  const result = structuredClone(semantics);
  for (const [index, members] of groups.entries())
    for (const key of members) result.fields.find((f: any) => f.key === key).group = `repair_group_${index}`;
  return result;
}

/** Repair relationships only, never text, field roles, geometry or branding. */
export async function repairSemanticGroups(input: {folder: string; output: string; assembled: string; index: number; layout: any; semantics: any; signal: AbortSignal}) {
  if (!needsGroupRepair(input.semantics.fields)) return input.semantics;
  const fields = input.semantics.fields.filter((f: any) => f.action === "replace").map((f: any) => {
    const s = input.layout.slots.find((s: any) => s.key === f.key);
    return {...f, x:s.x, y:s.y, w:s.w, h:s.h};
  });
  const fingerprint = digest({version:1,fields,composition:input.semantics.composition});
  const path = join(input.output, `semantic-groups-${input.index}.json`);
  try {
    const old = await readJson(path);
    if (old.fingerprint === fingerprint) return applySemanticGroups(old.data,input.semantics);
  } catch(e) { if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e; }
  const data = await llmJson({stage:`fill-groups-${input.index+1}`,folder:input.folder,signal:input.signal,
    images:[join(input.assembled,"previews",`slide-${input.index}.png`)],
    prompt:'Исправь только связи между существующими полями шаблона. Сейчас число и его подпись ошибочно могут иметь отдельные group, что не позволяет перенести единицу в подпись при переполнении. По изображению, координатам и назначению объедини число, единицу, название и пояснение ОДНОГО визуального блока в одну группу. Разные колонки/карточки не объединяй. Общий заголовок оставь отдельной группой. Если число действительно не имеет подписи, оставь самостоятельным. Существующие группы одного абзаца сохраняй, если они корректны. Верни {groups:[["key числа","key подписи","key пояснения"],["key заголовка"]]}. Каждый переданный ключ ровно один раз. Никаких текстов или координат в ответе.',
    payload:{composition:input.semantics.composition,fields},
    validate:raw=>{applySemanticGroups(raw,input.semantics);return raw;},
  });
  await writeJson(path,{fingerprint,data});
  return applySemanticGroups(data,input.semantics);
}
