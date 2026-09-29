type Field = {key:string; role:string; action:string; group:string};
type Slot = {key:string; x:number; y:number; w:number; h:number; shapeId:number; cell?:number[]};
const unitHeading = (text:string) => /^(?:ед|единиц[аы]|едизмерения|единицаизмерения|единицыизмерения|units?|unitofmeasure|unitofmeasurement|uom)$/iu.test(text.replace(/[\s.]/gu,''));
const number = (text:string) => /\d/u.test(text);

/** A template's sample number is not an immutable column type. A generated
 * unit column may contain words, provided its heading AND numeric row peer
 * establish that relationship. Numeric evidence and semantic QA still run. */
export function isLinkedUnitColumn(key:string,slots:Slot[],fields:Field[],data:any) {
  const slot=slots.find(s=>s.key===key),field=fields.find(f=>f.key===key);
  const text=data.fields[key]?.text || '';
  if(!slot||!field||!text.trim()||number(text))return false;
  const sameRow=(other:Slot)=>slot.cell&&other.cell
    ? slot.shapeId===other.shapeId&&slot.cell[0]===other.cell[0]
    : !slot.cell&&!other.cell&&Math.abs(slot.y+slot.h/2-other.y-other.h/2)<=Math.min(slot.h,other.h)*.55;
  const peers=slots.filter(s=>s.key!==key&&sameRow(s)&&fields.some(f=>f.key===s.key&&f.group===field.group&&f.role==='metric'&&f.action==='replace'));
  if(!peers.some(s=>number(data.fields[s.key]?.text||'')))return false;
  return slots.some(header=>{
    if(header.key===key||!fields.some(f=>f.key===header.key&&f.action==='replace')||!unitHeading(data.fields[header.key]?.text||''))return false;
    if(slot.cell||header.cell)return !!slot.cell&&!!header.cell&&slot.shapeId===header.shapeId&&header.cell[0]===0&&slot.cell[0]>0&&slot.cell[1]===header.cell[1];
    return header.y+header.h<=slot.y+1&&Math.abs(header.x+header.w/2-slot.x-slot.w/2)<=Math.min(header.w,slot.w)*.1;
  });
}
