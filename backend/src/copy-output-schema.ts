import type { Layout } from './domain.js';
const object=(properties:Record<string,any>)=>({type:'object',additionalProperties:false,required:Object.keys(properties),properties});
const strings={type:'array',items:{type:'string'}};
/** Constrain addresses, not prose length. Hard character caps in constrained
 * decoding caused broken word endings in real pilots. The model receives
 * measured writing targets; native fit and semantic review enforce quality. */
export function copyOutputSchema(layout:Layout, issues:any[], semantics:Array<{key:string;role:string;group:string}> = []) {
  const allowed=issues.length ? new Set<string>(issues.map(i=>i.key).filter(Boolean)) : undefined;
  const defs:Record<string,any>={field:object({text:{type:'string'},evidence:strings})};
  const fields:Record<string,any>={};
  // Adjacent XML IDs often belong to different visual blocks. Decode each
  // semantic group together, in visual order, rather than alternating captions
  // from one event with the metric of the next event.
  const ordered=semantics.length ? [...new Set(semantics.map(f=>f.group))].flatMap(group=>
    layout.slots.filter(s=>semantics.some(f=>f.key===s.key && f.group===group)).sort((a,b)=>a.y-b.y || a.x-b.x)) : [];
  const seen=new Set(ordered.map(s=>s.key));
  for(const slot of [...ordered,...layout.slots.filter(s=>!seen.has(s.key))]) {
    if(allowed&&!allowed.has(slot.key))continue;
    // The provider's strict schema subset does not permit siblings of $ref.
    // Roles and groups are already included in the prompt; keep this a pure ref.
    fields[slot.key]={$ref:'#/$defs/field'};
  }
  const charts:Record<string,any>={};
  for(const chart of layout.charts) {
    if(allowed&&!allowed.has(chart.key))continue;
    charts[chart.key]=object({title:{type:'string'},categories:{...strings,minItems:chart.pointCount,maxItems:chart.pointCount},
      series:{type:'array',minItems:chart.seriesCount,maxItems:chart.seriesCount,items:object({name:{type:'string'},values:{type:'array',minItems:chart.pointCount,maxItems:chart.pointCount,items:{type:'number'}}})},evidence:strings});
  }
  return {...object({fields:object(fields),charts:object(charts),...(issues.length?{repairAnalysis:object({cause:{type:'string'},action:{type:'string'},fields:{type:'array',items:{type:'string',enum:[...allowed!]}}})}:{})}),$defs:defs};
}
