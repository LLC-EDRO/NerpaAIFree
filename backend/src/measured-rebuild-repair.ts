import {retainsSourceLineContact} from './source-composition.js';
type Box = {x:number;y:number;w:number;h:number};
const area=(a:Box,b:Box)=>Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
const contains=(a:Box,b:Box)=>b.x>=a.x-.01&&b.y>=a.y-.01&&b.x+b.w<=a.x+a.w+.01&&b.y+b.h<=a.y+a.h+.01;

/** A bounded proposal, never an approval. The ordinary native fit must accept
 * it before use. Only transparent frames move; cards/photos/charts stay native. */
export function separateRebuiltFrames(scene:any,issues:any[],profile:any) {
  const next=structuredClone(scene);let changed=false;
  const anchors=new Map<string,any>((profile.textAnchors||[]).map((a:any)=>[a.key,a]));
  const safe=(candidate:any,previous:any)=>{
    const anchor=anchors.get(candidate.key);
    if(!anchor?.allowFrameOverflow||!profile.page||!contains(profile.page,candidate)||
      !contains(anchor.container||anchor.repairRegion||profile.page,candidate))return false;
    // A metric tied to the source baseline cannot be lifted on its own. If a
    // caption conflicts, repair that caption or let the model resolve the row.
    if((profile.metricAlignmentGroups||[]).some((g:any)=>g.keys.includes(candidate.key))&&
      Math.abs(candidate.y+candidate.h-previous.y-previous.h)>.01)return false;
    const obstacles=[...(profile.protectedBoxes||[]),...next.texts.filter((t:any)=>t.key!==candidate.key),
      ...next.pictures,...next.charts,...next.tables];
    for(const b of obstacles){
      if(retainsSourceLineContact(candidate,b,profile))continue;
      // Retain an existing full containment (a background behind a caption).
      if(contains(b,previous)&&contains(b,candidate))continue;
      if(area(candidate,b)>area(previous,b)+.5)return false;
    }
    for(const d of profile.decorations||[]){
      if(retainsSourceLineContact(candidate,{...d.box,shapeId:d.shapeId},profile))continue;
      if((candidate.allowUnderlayShapes||[]).includes(d.shapeId)&&(d.outlineOnly||contains(d.box,candidate)))continue;
      if(contains(d.box,previous)&&contains(d.box,candidate))continue;
      if(area(candidate,d.box)>area(previous,d.box)+.5)return false;
    }
    return true;
  };
  for(const issue of issues){
    if(!issue.details?.includes('rebuild_overlap')||!issue.blocker)continue;
    const a=next.texts.find((t:any)=>t.key===issue.key);
    const peer=issue.blocker.key&&next.texts.find((t:any)=>t.key===issue.blocker.key);
    // The text frame may slightly cross a fixed card/photo edge. Move only
    // the transparent text; never move or resize that native graphic.
    const fixed=Number.isInteger(issue.blocker.shapeId)&&[...next.pictures,...next.charts,...next.tables,...(profile.decorations||[]).map((d:any)=>({...d.box,shapeId:d.shapeId}))]
      .find((p:any)=>p.shapeId===issue.blocker.shapeId);
    const b=peer||fixed;
    if(!a||!b||area(a,b)<=.5)continue;
    if(retainsSourceLineContact(a,b,profile))continue;
    const proposals=[{...a,y:b.y+b.h+2},{...a,y:b.y-a.h-2},{...a,x:b.x+b.w+2},{...a,x:b.x-a.w-2}]
      .map(p=>({p,previous:a,obstacle:b}));
    if(peer)proposals.push(...[{...b,y:a.y+a.h+2},{...b,y:a.y-b.h-2},{...b,x:a.x+a.w+2},{...b,x:a.x-b.w-2}]
      .map(p=>({p,previous:b,obstacle:a})));
    proposals.sort((l,r)=>Math.hypot(l.p.x-l.previous.x,l.p.y-l.previous.y)-Math.hypot(r.p.x-r.previous.x,r.p.y-r.previous.y));
    const found=proposals.find(({p,previous,obstacle})=>{
      const original=anchors.get(previous.key),other=anchors.get(obstacle.key)||fixed;
      if(!original||!other)return false;
      // Preserve source column/timeline order, not just a collision-free box.
      if(p.x!==previous.x&&Math.sign(p.x+p.w/2-obstacle.x-obstacle.w/2)!==Math.sign(original.x+original.w/2-other.x-other.w/2))return false;
      if(p.y!==previous.y&&Math.sign(p.y+p.h/2-obstacle.y-obstacle.h/2)!==Math.sign(original.y+original.h/2-other.y-other.h/2))return false;
      return safe(p,previous);
    });
    if(found){Object.assign(found.previous,found.p);changed=true;}
  }
  return changed?next:undefined;
}

// Exact typographic equivalents only, after the native font reports the
// unsupported character AND proves the replacement glyphs exist in that face.
export function replaceUnsupportedGlyphs(data:any,issues:any[],preserved:Set<string>=new Set()) {
  const candidate=structuredClone(data);let changed=false;
  for(const issue of issues){
    if(preserved.has(issue.key)||!issue.details?.includes('native_source_font_missing_glyph'))continue;
    const field=candidate.fields[issue.key];if(!field)continue;
    for(const [from,to] of Object.entries(issue.characterReplacements||{})){
      if(typeof to!=='string'||!glyphEquivalents[from]||to!==glyphEquivalents[from])continue;
      if(field.text.includes(from)){field.text=field.text.split(from).join(to);changed=true;}
    }
  }
  return changed?candidate:undefined;
}
const glyphEquivalents:Record<string,string>={'→':'->','←':'<-','↔':'<->','⇒':'=>','≤':'<=','≥':'>=','\u2011':'-','\u202f':' ','\u00a0':' '};
