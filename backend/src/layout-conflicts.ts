/** Candidate intersections are data for the existing vision call, never an
 * automatic order to redesign a slide. AI must confirm the visible conflict. */
export function layoutConflictCandidates(layout: any) {
  const objects=[...layout.slots.filter((s:any)=>s.text?.trim()),...(layout.charts || [])];
  const result:{keys:string[];overlapPt:{w:number;h:number}}[]=[];
  for(let i=0;i<objects.length;i++)for(const b of objects.slice(i+1)){
    const a=objects[i];
    if(a.shapeId===b.shapeId)continue;
    if(![a.x,a.y,a.w,a.h,b.x,b.y,b.w,b.h].every(Number.isFinite))continue;
    const w=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x),h=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
    if(w>2&&h>2)result.push({keys:[a.key,b.key],overlapPt:{w:Math.round(w),h:Math.round(h)}});
  }
  return result.slice(0,60);
}

export function confirmedLayoutIssues(layout:any, semantics:any) {
  const candidates=layoutConflictCandidates(layout);
  const allowed=new Set(candidates.flatMap(c=>c.keys));
  return (semantics.layoutIssues || []).filter((i:any)=>i.keys.length>=2 && i.keys.every((k:string)=>allowed.has(k)) &&
    candidates.some(c=>c.keys.every(k=>i.keys.includes(k))));
}

export function earlyLayoutRepairIssues(layout:any,semantics:any) {
  const confirmed = confirmedLayoutIssues(layout,semantics).flatMap((issue:any)=>issue.keys
    .filter((key:string)=>layout.slots.some((s:any)=>s.key===key))
    .map((key:string)=>({key,reason:'overflow',details:['source_layout_conflict'],instruction:issue.instruction,
      conflictingKeys:issue.keys})));
  // Native table rows cannot be repaired by shortening an unrelated text field.
  // Hand the source conflict to AI before buying fill/render retries.
  const sourceTableIssues=(layout.contentFitIssues || []).filter((issue:any)=>
    issue.reason==='overflow' && layout.slots.some((s:any)=>s.key===issue.key && s.cell));
  const tableOverlaps=layoutConflictCandidates(layout).filter(c=>{
    const slots=c.keys.map(k=>layout.slots.find((s:any)=>s.key===k));
    return slots.every(Boolean) && !!slots[0].cell!==!!slots[1].cell && slots.every(s=>
      semantics.fields?.find((f:any)=>f.key===s.key)?.action==='replace');
  }).flatMap(c=>c.keys.map(key=>({key,reason:'overflow',details:['source_layout_conflict'],conflictingKeys:c.keys,
    instruction:'Проверь исходное пересечение отдельного текстового поля с заполненными ячейками таблицы. Сохрани стиль, отдели подпись от таблицы; не меняй число колонок.'})));
  return [...new Map([...confirmed,...sourceTableIssues,...tableOverlaps].map(i=>[i.key,i])).values()];
}

/** Rewording alone cannot separate two visibly colliding objects. */
export function requiresLayoutRepair(issues:any[]) {
  return issues.some(i=>i.details?.some((d:string)=>['rendered_text_overlap','rendered_text_overlaps_source_text','rendered_text_overlaps_reserved_object','source_picture_placeholder_occludes_text'].includes(d)));
}
