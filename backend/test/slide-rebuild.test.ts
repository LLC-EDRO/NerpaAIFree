import test from "node:test";
import assert from "node:assert/strict";
import { rebuildLayout, shouldRebuild } from "../src/slide-rebuild.js";
import { needsIndividualRepairPreview } from '../src/slide-rebuild.js';
test('only genuine visibility risks need another individual render before final deck QA',()=>{
 assert.equal(needsIndividualRepairPreview([{details:['rendered_text_outside_frame']}]),false);
 assert.equal(needsIndividualRepairPreview([{details:['rendered_text_overlaps_reserved_object'],blockerRole:'decoration'}]),false);
 for(const details of [['rendered_text_missing'],['rendered_text_outside_page'],['rendered_text_overlap'],['rendered_text_overlaps_reserved_object']])
  assert.equal(needsIndividualRepairPreview([{details,blockerRole:'native_object'}]),true);
});
const original: any = { id: "any-layout", slots: [], charts: [] };
const profile = {
  fonts: ["Arial"],
  colors: ["#163248"],
  pictures: [],
  charts: [],
  tables: [{ shapeId: 71, columns: 2 }],
  fingerprint: "test",
};
const proposal = () => ({
  reason: "Frames overlap",
  texts: [
    {
      key: "r_title",
      x: 30,
      y: 30,
      w: 600,
      h: 50,
      font: "Arial",
      size: 30,
      color: "#163248",
      bold: true,
      align: "left",
    },
  ],
  tables: [
    {
      shapeId: 71,
      x: 30,
      y: 100,
      w: 600,
      h: 200,
      font: "Arial",
      size: 18,
      color: "#163248",
      headers: ["r_h1", "r_h2"],
      rows: [["r_a", "r_b"]],
    },
  ],
  pictures: [],
  charts: [],
  data: {
    fields: {
      r_title: { text: "Тема", evidence: [] },
      r_h1: { text: "Район" },
      r_h2: { text: "Статус" },
      r_a: { text: "Север" },
      r_b: { text: "Готов" },
    },
    charts: {},
  },
});
test('rebuilt source step badges cannot be reassigned to another source field',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 const p:any=proposal();p.tables=[];
 const values=['01.','02.','03.'];
 p.texts=values.map((_,i)=>({...p.texts[0],key:i===0?'r_title':`r_badge_${i}`,x:20+i*200,w:100}));
 p.data={fields:Object.fromEntries(p.texts.map((t:any,i:number)=>[t.key,{text:['03','01','02'][i],evidence:[]}])),charts:{}};
 const slots=values.map((text,i)=>({key:`source_${i}`,text,role:'label',shapeId:100+i}));
 const anchors=p.texts.map((t:any,i:number)=>({...t,sourceKey:slots[i].key,shapeId:slots[i].shapeId}));
 const result=rebuildLayout(p,{id:'different-template',slots,charts:[]},{...profile,version:7,tables:[],textAnchors:anchors});
 assert.deepEqual(Object.values<any>(result.proposal.data.fields).map(f=>f.text),values);
 assert.deepEqual(validateCopy(result.proposal.data,result.layout,result.semantics,'').issues,[]);
});
test("recomposition maps each table cell separately and retains native object identity", () => {
  const result = rebuildLayout(proposal(), original, profile);
  assert.equal(result.layout.slots.length, 5);
  assert.deepEqual(result.layout.slots.at(-1)?.cell, [1, 1]);
  assert.equal(result.scene.fingerprint, "test");
});
test("model code and removal of native table are rejected; unavailable fonts are substituted", () => {
  assert.throws(() =>
    rebuildLayout({ ...proposal(), code: "run()" }, original, profile),
  );
  assert.throws(() =>
    rebuildLayout({ ...proposal(), tables: [] }, original, profile),
  );
  const p = proposal();
  p.texts[0].font = "Comic Sans";
  assert.equal(rebuildLayout(p, original, profile).scene.texts[0].font, 'Arial');
});

test('native cell aliases and unavailable table font recover without losing generated values',()=>{
  const p:any=proposal();p.tables[0].font='Calibri';
  const names=['s71_r0_c0','s71_r0_c1','s71_r1_c0','s71_r1_c1'];
  const old=['r_h1','r_h2','r_a','r_b'];
  old.forEach((key,i)=>{p.data.fields[names[i]]=p.data.fields[key];delete p.data.fields[key];});
  p.tables[0].headers=names.slice(0,2);p.tables[0].rows=[names.slice(2)];
  const layout={...original,slots:names.map((key,i)=>({key,shapeId:71,cell:[Math.floor(i/2),i%2],font:'Arial'}))};
  const result=rebuildLayout(p,layout,profile);
  assert.equal(result.scene.tables[0].font,'Arial');
  assert.equal(result.proposal.data.fields.r_s71_r1_c0.text,'Север');
  assert.equal(result.scene.tables[0].rows[1][0],'r_s71_r1_c0');
});
test("a factual-only failure never triggers redesign, geometric history does", () => {
  assert.equal(shouldRebuild([{ reason: "unsupported_number" }], []), false);
  assert.equal(
    shouldRebuild([{ reason: "repeated_value" }], [{ reason: "overflow" }]),
    true,
  );
});

test("citation wrapper normalization requires a verbatim source quote", async () => {
  const { sourceQuotation } = await import("../src/domain.js");
  const source = "Северный 2150, Южный 2040.";
  assert.equal(
    sourceQuotation("userSource: «Северный 2150»", source),
    "Северный 2150",
  );
  assert.equal(
    sourceQuotation("userSource: «Северный 9999»", source),
    "userSource: «Северный 9999»",
  );
  assert.equal(
    sourceQuotation("На севере 2150 обращений", source),
    "На севере 2150 обращений",
  );
});

test('last-resort geometry patches preserve unaffected text, table cells and template style',async()=>{
 const {patchRebuiltText}=await import('../src/slide-rebuild.js');
 const initial=rebuildLayout(proposal(),original,profile);
 const prior={reason:'Original frames conflict',scene:initial.scene,data:initial.proposal.data};
 const result=patchRebuiltText({reason:'Increase title height',changes:[{key:'r_title',x:30,y:20,w:600,h:65}]},prior,new Set(['r_title']),original,profile);
 assert.equal(result.scene.texts[0].h,65);assert.deepEqual(result.proposal.data,initial.proposal.data);assert.deepEqual(result.scene.tables,initial.scene.tables);
 assert.throws(()=>patchRebuiltText({reason:'Bad target',changes:[{key:'r_h1',x:0,y:0,w:500,h:50}]},prior,new Set(['r_title']),original,profile));
});

test('contiguous step heading numbers are structure, but numbers within headings remain factual',async()=>{
 const {validateCopy}=await import('../src/domain.js');const p:any=proposal();
 p.texts.push({...p.texts[0],key:'r_step1',y:310,h:35},{...p.texts[0],key:'r_step2',y:355,h:35});
 p.data.fields.r_step1={text:'1. Приём обращения',evidence:[]};p.data.fields.r_step2={text:'2. Проверить 999 адресов',evidence:[]};
 const result=rebuildLayout(p,original,profile);const issues=validateCopy(result.proposal.data,result.layout,result.semantics,'',[]).issues;
 assert.equal(issues.some(i=>i.key==='r_step1'),false);assert.ok(issues.some(i=>i.key==='r_step2' && i.reason==='unsupported_number'));
});


test('explicit consecutive step markers do not require factual evidence; arbitrary numeric labels still do',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 for (const [values,valid] of [[['1','2','3'],true],[['1','2','999'],false],[['3','4','5'],false]] as const) {
  const p:any=proposal();
  values.forEach((value,i)=>{const key=`r_n${i}`;p.texts.push({...p.texts[0],key,role:'step_number',y:310+i*40,h:35});p.data.fields[key]={text:value,evidence:[]};});
  const result=rebuildLayout(p,original,profile);
  const issues=validateCopy(result.proposal.data,result.layout,result.semantics,'',[]).issues;
  assert.equal(issues.some(i=>i.reason==='unsupported_number'),!valid);
 }
});

test('failed local rebuild patches can switch to a new layout instead of repeating the same grid',async()=>{
 const {useLocalRebuildPatch,proposalFromRebuildAttempt}=await import('../src/slide-rebuild.js');
 const initial=rebuildLayout(proposal(),original,profile);
 const previous={reason:'Frame overflow',scene:initial.scene,data:initial.proposal.data,issues:[{key:'r_title',reason:'overflow'}]};
 assert.equal(useLocalRebuildPatch(3,previous),true);
 assert.equal(useLocalRebuildPatch(4,previous),false);
 assert.equal(useLocalRebuildPatch(5,previous),true);
 assert.deepEqual(rebuildLayout(proposalFromRebuildAttempt(previous),original,profile).scene,initial.scene);
});

test('source design retains field identities, individual typography and graphic anchors', () => {
 const p:any=proposal(); const anchor={...p.texts[0],sourceKey:'title_original',shapeId:91,size:10.5,bold:false};
 const fixed={shapeId:88,box:{x:25,y:270,w:600,h:5}};
 const sourceProfile={...profile,version:2,textAnchors:[anchor],pictures:[fixed],decorations:[]};
 p.pictures=[{shapeId:88,x:100,y:300,w:300,h:20}];
 const result=rebuildLayout(p,original,sourceProfile);
 assert.equal(result.scene.texts[0].size,10.5);
 assert.equal(result.scene.texts[0].bold,false);
 assert.deepEqual(result.scene.pictures[0],{shapeId:88,...fixed.box});
 p.texts.push({...p.texts[0],key:'r_free_paragraph'});
 assert.throws(()=>rebuildLayout(p,original,sourceProfile),/keep_source_fields/);
 p.texts=p.texts.slice(1);
 assert.throws(()=>rebuildLayout(p,original,sourceProfile),/keep_source_fields/);
});

test('repairing one heading preserves alignment of its source row peers',()=>{
 const p:any=proposal(); p.tables=[];
 p.texts.push({...p.texts[0],key:'r_peer',x:360,w:200});p.texts[0].w=200;
 p.data.fields.r_peer={text:'Peer',evidence:[]};
 const anchors=structuredClone(p.texts);
 p.texts[0].y=15;p.texts[0].h=65;
 const result=rebuildLayout(p,original,{...profile,tables:[],version:3,textAnchors:anchors});
 assert.equal(result.scene.texts[0].y,15);assert.equal(result.scene.texts[1].y,15);
 assert.equal(result.scene.texts[1].h,65);
});

test('contrast repair changes only diagnosed colors, preserving all copy and geometry',async()=>{
 const {patchRebuiltContrast,patchRebuiltText}=await import('../src/slide-rebuild.js');
 const p:any=proposal();const anchors=structuredClone(p.texts);
 const sourceProfile={...profile,version:3,textAnchors:anchors,colors:['#163248','#000000'],decorations:[]};
 const originalResult=rebuildLayout(p,original,sourceProfile);
 const result=patchRebuiltContrast({reason:'Improve contrast',changes:[{key:'r_title',color:'#000000'}]},originalResult,new Set(['r_title']),original,sourceProfile);
 assert.equal(result.scene.texts[0].color,'#000000');
 assert.deepEqual(result.scene.texts[0],{...originalResult.scene.texts[0],color:'#000000'});
 assert.deepEqual(result.proposal.data,originalResult.proposal.data);
 assert.deepEqual(result.scene.tables,originalResult.scene.tables);
 assert.throws(()=>patchRebuiltContrast({reason:'wrong field',changes:[{key:'r_h1',color:'#000000'}]},originalResult,new Set(['r_title']),original,sourceProfile));
 assert.throws(()=>patchRebuiltContrast({reason:'foreign color',changes:[{key:'r_title',color:'#FF0000'}]},originalResult,new Set(['r_title']),original,sourceProfile));
 const next=patchRebuiltText({reason:'Add headroom',changes:[{key:'r_title',x:30,y:20,w:600,h:60}]},{scene:result.scene,reason:'Contrast fixed',data:result.proposal.data},new Set(['r_title']),original,sourceProfile);
 assert.equal(next.scene.texts[0].color,'#000000');
});

test('a changed render policy can recheck an exhausted journal without more model rewrites',async()=>{
 const {rebuildAttemptLimit}=await import('../src/slide-rebuild.js');
 assert.equal(rebuildAttemptLimit(6,true),7);
 assert.equal(rebuildAttemptLimit(6,false),6);
 assert.equal(rebuildAttemptLimit(2,true),6);
});

test('provider failure can repair contrast only on a measured solid background using template colors',async()=>{
 const {contrastFallback}=await import('../src/slide-rebuild.js');
 const warn={reason:'rendered_text_low_contrast',key:'any',backgroundColor:'#FFFFFF',backgroundCoverage:.96};
 assert.deepEqual(contrastFallback([warn],['#1EFF8F','#000533']),[{key:'any',color:'#000533'}]);
 assert.deepEqual(contrastFallback([{...warn,backgroundColor:'#000000'}],['#FFFFFF','#000533']),[{key:'any',color:'#FFFFFF'}]);
 assert.deepEqual(contrastFallback([{...warn,backgroundCoverage:.3}],['#000533']),[]);
 assert.deepEqual(contrastFallback([warn],['#FFFFFF']),[]);
});

test('rebuilt step headings retain zero-padded structural numbering and validate inner facts',async()=>{
 const {validateCopy}=await import('../src/domain.js');
 for (const labels of [['01 Начните','02 Продолжите'],['1 Начните','2 Продолжите']]) {
  const p:any=proposal();
  labels.forEach((label,i)=>{const key=`r_heading${i}`;p.texts.push({...p.texts[0],key,role:'step_number',y:310+i*40,h:35});p.data.fields[key]={text:label,evidence:[]};});
  const result=rebuildLayout(p,original,profile);
  assert.deepEqual(validateCopy(result.proposal.data,result.layout,result.semantics,'',[]).issues,[]);
  result.proposal.data.fields.r_heading1.text += ' 999 сотрудников';
  assert.ok(validateCopy(result.proposal.data,result.layout,result.semantics,'',[]).issues.some(i=>i.reason==='unsupported_number' && i.unsupportedNumbers.includes('999')));
 }
});

test('source design ordinals survive full rebuilding and local text patches',async()=>{
 const {patchRebuiltText}=await import('../src/slide-rebuild.js');
 const p:any=proposal();
 for(const [i,label] of ['Начните','Продолжите'].entries()) {
  const key=`r_source${i}`;p.texts.push({...p.texts[0],key,x:30+i*310,y:310,w:290,h:45});p.data.fields[key]={text:label,evidence:[]};
 }
 const source:any={...original,slots:[{key:'source0',text:'01Организатор',role:'label'},{key:'source1',text:'02Структура',role:'label'}]};
 const nativeProfile={...profile,version:3,textAnchors:p.texts.map((t:any)=>({...t,sourceKey:t.key.slice(2)}))};
 const initial=rebuildLayout(p,source,nativeProfile);
 assert.equal(initial.proposal.data.fields.r_source0.text,'01 Начните');
 assert.equal(initial.proposal.data.fields.r_source1.text,'02 Продолжите');
 const changed=patchRebuiltText({reason:'Shorten text',changes:[{key:'r_source0',x:30,y:310,w:290,h:45,text:'Проверьте'}]},{scene:initial.scene,reason:'Initial',data:initial.proposal.data},new Set(['r_source0']),source,nativeProfile);
 assert.equal(changed.proposal.data.fields.r_source0.text,'01 Проверьте');
 assert.equal(changed.proposal.data.fields.r_source1.text,'02 Продолжите');
});

test('AI can moderately reduce text size inside source design, without changing font or contrast', () => {
  const p:any=proposal();const anchor={...p.texts[0],sourceKey:'heading',shapeId:123};
  const nativeProfile={...profile,version:3,textAnchors:[anchor],decorations:[]};
  p.texts[0].size=24;
  const result=rebuildLayout(p,original,nativeProfile);
  assert.equal(result.scene.texts[0].size,24);
  assert.equal(result.scene.texts[0].color,anchor.color);
  p.texts[0].size=2;
  assert.equal(rebuildLayout(p,original,nativeProfile).scene.texts[0].size,21);
});

test('measured font fallback preserves content geometry and caps shrinking, ignores unrelated errors', async () => {
  const { fittedRebuildSizes } = await import('../src/slide-rebuild.js');
  const scene={texts:[{key:'r_title',size:30,x:1,y:2,w:100,h:40}]};
  const profile={textAnchors:[{key:'r_title',size:30}]};
  const result=fittedRebuildSizes(scene,[{key:'r_title',reason:'overflow',details:['source_text_frame_overflow'],measuredHeightPt:80,availableHeightPt:40}],profile);
  assert.deepEqual(result.texts[0],{...scene.texts[0],size:21});
  assert.equal(scene.texts[0].size,30);
  assert.equal(fittedRebuildSizes(scene,[{key:'r_title',reason:'unsupported_number'}],profile),undefined);
});

test('recomposition preserves source vertical center and accepts justified independent alignment',()=>{
 const p:any=proposal();const anchor={...p.texts[0],verticalAlign:'center',sourceKey:'heading',shapeId:123,h:200};
 const nativeProfile={...profile,version:3,textAnchors:[anchor],decorations:[]};
 assert.equal(rebuildLayout(p,original,nativeProfile).scene.texts[0].verticalAlign,'center');
 p.texts[0].alignment={horizontal:'center',vertical:'preserve',reason:'Single title in a centered card'};
 const result=rebuildLayout(p,original,nativeProfile);
 assert.equal(result.scene.texts[0].align,'center');
 assert.equal(result.scene.texts[0].verticalAlign,'center');
 assert.equal(result.scene.texts[0].font,anchor.font);
});

test('alignment selected during template analysis survives a later slide repair',()=>{
 const p:any=proposal();const anchor={...p.texts[0],verticalAlign:'top',sourceKey:'heading',shapeId:123,h:200};
 const selected={horizontal:'preserve',vertical:'center',reason:'Standalone label inside a tall shape'};
 const source={...original,textAlignment:{heading:selected}};
 const result=rebuildLayout(p,source,{...profile,version:3,textAnchors:[anchor],decorations:[]});
 assert.equal(result.scene.texts[0].verticalAlign,'center');
 assert.equal(result.scene.texts[0].align,anchor.align);
 assert.deepEqual(result.scene.texts[0].alignment,selected);
});
test('recomposition retains metric-label semantic grouping from arbitrary source keys',()=>{
  const p=proposal();
  p.texts.push({...p.texts[0],key:'r_value',y:90});p.texts.push({...p.texts[0],key:'r_label',y:150});
  p.data.fields= {...p.data.fields,r_value:{text:'3'},r_label:{text:'шарнира под замену'}} as any;
  const source={...original,rebuildSemantics:{fields:[{key:'native_value',role:'metric',group:'hardware',intent:'Количество заменяемых шарниров'},{key:'native_caption',role:'metric_label',group:'hardware',intent:'Предмет счёта'}]}};
  const result=rebuildLayout(p,source,{...profile,textAnchors:[{key:'r_value',sourceKey:'native_value'},{key:'r_label',sourceKey:'native_caption'}]});
  assert.equal(result.semantics.fields.find(f=>f.key==='r_value')?.group,'hardware');
  assert.equal(result.semantics.fields.find(f=>f.key==='r_label')?.group,'hardware');
  assert.equal(result.semantics.fields.find(f=>f.key==='r_value')?.intent,'Количество заменяемых шарниров');
});

test('Rebuilt native tables keep the individual source header and body colors',()=>{
 const src:any={...original,slots:[{key:'h1',shapeId:71,cell:[0,0],color:'#FFFFFF',font:'Arial',bold:true,align:'center'},{key:'h2',shapeId:71,cell:[0,1],color:'#FFFFFF',font:'Arial',bold:true,align:'center'},{key:'b1',shapeId:71,cell:[1,0],color:'#163248',font:'Arial',bold:false,align:'left'},{key:'b2',shapeId:71,cell:[1,1],color:'#163248',font:'Arial',bold:false,align:'left'}]};
 const result=rebuildLayout(proposal(),src,{...profile,colors:['#163248','#FFFFFF']});
 assert.equal(result.layout.slots.find(s=>s.key==='r_h1')?.color,'#FFFFFF');
 assert.equal(result.layout.slots.find(s=>s.key==='r_a')?.color,'#163248');
 assert.equal(result.scene.tables[0].cellStyles.r_h1.align,'center');
});
