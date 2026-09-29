import { join } from 'node:path';
import { readJson } from './store.js';
import { nativeSlide } from './domain.js';
import { qualityWarnings } from './quality-warnings.js';

/** Reuse actual generated content. Never invent content to satisfy a validator. */
export async function recoverSlideWithWarnings(input: {
  output: string; assembled: string; index: number; layout: any; title: string;
  previous?: any; issues: any[]; signal: AbortSignal;
  native: (action: string, folder: string, extra: any, signal?: AbortSignal) => Promise<any>;
}) {
  input.signal.throwIfAborted();
  const load = async (name: string) => {
    try { return await readJson(join(input.output,name)); }
    catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e; return undefined; }
  };
  const state = await load(`rebuild-${input.index}.json`);
  const copy = await load(`copy-${input.index}.json`);
  const candidates: Array<{slide:any;data:any;issues:any[]}> = [];
  // A late failed patch must not erase an earlier usable filled slide. Rank
  // all bounded attempts by known problems, preferring recent equal scores.
  const ranked=(state?.attempts || []).map((attempt:any,index:number)=>({attempt,index}))
    .sort((a:any,b:any)=>(a.attempt.issues?.length || 0)-(b.attempt.issues?.length || 0) || b.index-a.index);
  for (const {attempt} of ranked) {
    if (!attempt.scene || !attempt.data?.fields) continue;
    candidates.push({data:attempt.data,issues:attempt.issues || [],slide:{title:input.title,native:{
      sourceSlideId:input.layout.id, mode:'rebuild', preserveTemplate:true, ordinal:input.index+1,
      rebuild:attempt.scene, fields:Object.fromEntries(Object.entries<any>(attempt.data.fields).map(([k,v])=>[k,v.text])),
      charts:Object.fromEntries(Object.entries<any>(attempt.data.charts || {}).map(([k,{evidence,...v}])=>[k,v])),
    }}});
  }
  const sourceOnly = copy?.rebuildNative?.native?.preserveSource || copy?.sourceFallback;
  const data = sourceOnly ? undefined : copy?.data || input.previous;
  const sourceKeys=new Set(input.layout.slots.map((s:any)=>s.key));
  if (data?.fields && Object.keys(data.fields).length===sourceKeys.size && Object.keys(data.fields).every(k=>sourceKeys.has(k)))
    candidates.push({data,issues:copy?.issues || input.issues,slide:nativeSlide(data,input.layout,input.title,input.index+1)});
  for (const candidate of candidates) {
    try {
      const fit = await input.native('fit',input.assembled,{slides:[candidate.slide]},input.signal);
      input.signal.throwIfAborted();
      return {...candidate,notice:{slide:input.index+1,reason:'Сохранён заполненный слайд с замечаниями',warnings:qualityWarnings([...input.issues,...candidate.issues,...(fit.issues||[])],input.index+1)}};
    } catch { input.signal.throwIfAborted(); }
  }
  // Keep a valid native source page when even applying a proposal fails.
  // Semantic clearing, table expansion and alignment work may have changed the
  // in-memory layout. Preservation must use the immutable assembled source,
  // otherwise its fields no longer match the actual PPTX and export rejects it.
  const analysis=await readJson(join(input.assembled,'analysis.json'));
  const source=analysis.layouts.find((layout:any)=>layout.id===input.layout.id);
  if (!source) throw new Error('pptx_source_layout_missing');
  const original = {fields:Object.fromEntries(source.slots.map((s:any)=>[s.key,{text:s.text || '',evidence:[]}])),charts:{}};
  const slide = nativeSlide(original,source,input.title,input.index+1);
  Object.assign(slide.native,{preserveSource:true,safeTextExpansion:false});
  delete slide.native.geometryRepairs;
  delete slide.native.tableRows;
  delete slide.native.tableColumns;
  delete (slide.native as Partial<typeof slide.native>).textAlignment;
  delete (slide.native as Partial<typeof slide.native>).alignmentIntent;
  return {slide,data:original,issues:input.issues,notice:{slide:input.index+1,reason:'Исходный слайд сохранён для ручного заполнения',warnings:qualityWarnings([{reason:'source_slide_preserved'},...input.issues],input.index+1)}};
}
