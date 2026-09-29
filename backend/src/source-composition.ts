type Box={x:number;y:number;w:number;h:number};
const inside=(a:Box,b:Box)=>a.x>=b.x-.01&&a.y>=b.y-.01&&a.x+a.w<=b.x+b.w+.01&&a.y+a.h<=b.y+b.h+.01;

/** Native source evidence, never an AI-provided overlap waiver. */
export function retainsSourceLineContact(text:any,other:any,profile:any) {
  const anchor=profile.textAnchors?.find((a:any)=>a.key===text.key);
  return (anchor?.sourceLineContacts||[]).some((c:any)=>{
    if(c.shapeId!==other.shapeId||['x','y','w','h'].some(k=>Math.abs(other[k]-c.box[k])>.01)||
      !inside(text,anchor.container||anchor.repairRegion||profile.page))return false;
    const [pos,size]=c.axis==='horizontal'?['y','h']:['x','w'];
    const overlap=(b:any)=>Math.max(0,Math.min(b[pos]+b[size],c.box[pos]+c.box[size])-Math.max(b[pos],c.box[pos]));
    return overlap(text)<=overlap(anchor)+.5;
  });
}

/** Keep source-proven metric rows coherent through both AI and measured fit.
 * Only groups derived from matching numeric source anchors over rails qualify.
 * The usual native fit/render still validates every resulting placement. */
export function preserveMetricComposition(texts:any[],profile:any) {
  const next=structuredClone(texts);
  for(const group of profile.metricAlignmentGroups||[]) {
    const peers=group.keys.map((key:string)=>next.find(t=>t.key===key));
    const anchors=group.keys.map((key:string)=>profile.textAnchors.find((a:any)=>a.key===key));
    if(peers.length<2||peers.some((p:any)=>!p)||anchors.some((a:any)=>!a))continue;
    const size=Math.min(...peers.map((p:any)=>p.size));
    const candidates=peers.map((p:any,i:number)=>({...p,size,
      y:anchors[i].y+anchors[i].h-p.h,verticalAlign:'bottom'}));
    if(candidates.some((p:any,i:number)=>size<(anchors[i].minSize||1)||!profile.page||
      !inside(p,profile.page)||!inside(p,anchors[i].container||anchors[i].repairRegion||profile.page)))continue;
    candidates.forEach((p:any,i:number)=>{
      // A cosmetic centering suggestion cannot detach a metric from its rail.
      if(p.alignment)p.alignment={...p.alignment,vertical:'preserve'};
      Object.assign(peers[i],p);
    });
  }
  return next;
}

export const sourceCompositionPrompt='profile.sourceLineContacts у текстовых якорей описывает подтверждённые исходные пересечения с тонкими линиями ПОЗАДИ текста: сохраняй этот приём, не поднимай число над линией ради удаления пересечения рамок. profile.metricAlignmentGroups связывает равноценные показатели: сохраняй исходную нижнюю линию рамок и одинаковый кегль всей группы. Если одному числу тесно, сначала расширь рамку по ширине в свободном месте; при уменьшении шрифта согласуй всю группу. При изменении высоты нижний край остаётся на исходной отметке. Обычные фотографии и новые пересечения текста этим исключением не покрываются.';
