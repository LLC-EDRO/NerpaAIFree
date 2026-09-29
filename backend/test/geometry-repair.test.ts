import test from 'node:test';
import assert from 'node:assert/strict';
import {parseGeometryProposal, geometryAttemptAvailable, geometryBounds, repairGeometry} from '../src/geometry-repair.js';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {repeatedGeometryIssues} from '../src/repair.js';
test('geometry proposals cannot edit other fields or typography',()=>{
 const frame={key:'title',x:10,y:20,w:200,h:40};
 assert.equal(parseGeometryProposal({cause:'height',action:'grow',frames:[frame]},new Set(['title'])).frames.length,1);
 for(const f of [{...frame,key:'logo'},{...frame,fontSize:10},{...frame,w:NaN}])assert.throws(()=>parseGeometryProposal({cause:'height',action:'grow',frames:[f]},new Set(['title'])));
});
test('unified patches reject executable/unknown properties and protected text',()=>{
 const patch={cause:'fit',action:'shorten and grow',frames:[],fields:[{key:'title',text:'Краткий тезис',evidence:[]}]};
 assert.equal(parseGeometryProposal(patch,new Set(),new Set(['title'])).fields.length,1);
 assert.throws(()=>parseGeometryProposal(patch,new Set()));
 assert.throws(()=>parseGeometryProposal({...patch,code:'execute()'},new Set(),new Set(['title'])));
 assert.throws(()=>parseGeometryProposal({...patch,fields:[...patch.fields,...patch.fields]},new Set(),new Set(['title'])));
});
test('AI can shorten text and expand a frame atomically; native rejection rolls both back',async()=>{
 for(const rejectNative of [false,true]){
  const folder=await mkdtemp(join(tmpdir(),'nerpa-joint-patch-'));
  try{
   const data={fields:{title:{text:'Длинный первоначальный тезис',evidence:[]}},charts:{}};
   const layout:any={id:'arbitrary',index:0,slots:[{key:'title',shapeId:31,role:'title',text:'Example',x:10,y:10,w:80,h:20,size:18}],charts:[]};
   const issue={key:'title',reason:'overflow',details:['source_text_frame_overflow']};let models=0,checks=0;
   const result=await repairGeometry({folder,output:folder,assembled:folder,index:0,title:'Тема',layout,data,issues:[issue],signal:new AbortController().signal,
    textRepair:{keys:['title'],context:{},validate:next=>({data:next,issues:[]})},
    model:(async(input:any)=>{models++;return input.validate({cause:'short frame',action:'rewrite and grow',frames:[{key:'title',x:10,y:10,w:100,h:25}],fields:[{key:'title',text:'Краткий тезис',evidence:[]}]});}) as any,
    native:async(_action,_folder,payload)=>{
      if(payload.repairOptions)return {issues:[issue],geometryOptions:[{fields:[{key:'title',box:{x:10,y:10,w:80,h:20}}]}]};
      checks++;assert.equal(layout.geometryRepairs.title.w,100);
      assert.equal(payload.slides[0].native.fields.title,'Краткий тезис');
      return {issues:rejectNative?[issue]:[]};
    },
   });
   assert.equal(data.fields.title.text,'Длинный первоначальный тезис');
   if(rejectNative){assert.equal(result,undefined);assert.equal(layout.geometryRepairs,undefined);assert.equal(models,2);}
   else{assert.equal(result?.data.fields.title.text,'Краткий тезис');assert.equal(layout.geometryRepairs.title.w,100);assert.equal(models,1);assert.equal(checks,1);}
  }finally{await rm(folder,{recursive:true,force:true});}
 }
});
test('unsupported content in a joint patch is rejected before applying native geometry',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'nerpa-joint-facts-'));
 try{
  const data={fields:{title:{text:'Исходный тезис',evidence:[]}},charts:{}};
  const layout:any={id:'free',slots:[{key:'title',shapeId:8,role:'title'}],charts:[]};
  const issue={key:'title',reason:'overflow',details:['source_text_frame_overflow']};let nativeCalls=0;
  const result=await repairGeometry({folder,output:folder,assembled:folder,index:0,title:'Тема',layout,data,issues:[issue],signal:new AbortController().signal,
   textRepair:{keys:['title'],context:{},validate:next=>({data:next,issues:[{key:'title',reason:'unsupported_number'}]})},
   model:(async(input:any)=>input.validate({cause:'fit',action:'rewrite',frames:[],fields:[{key:'title',text:'99% успеха',evidence:[]}]})) as any,
   native:async()=>{nativeCalls++;return {issues:[issue],geometryOptions:[{fields:[]}]};},
  });
  assert.equal(result,undefined);assert.equal(nativeCalls,1);assert.equal(layout.geometryRepairs,undefined);assert.equal(data.fields.title.text,'Исходный тезис');
 }finally{await rm(folder,{recursive:true,force:true});}
});
test('old geometry rejection no longer bans unchanged text after verified frame repair',()=>{
 assert.deepEqual(repeatedGeometryIssues({fields:{title:{text:'Same title'}}},[{key:'title',reason:'overflow',value:'Same title',geometrySuperseded:true}]),[]);
});
test('a rendered collision is not declared repaired just because rewritten text fits',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'nerpa-render-collision-'));
 try{
  const layout:any={id:'generic',slots:[{key:'body',shapeId:18,role:'body',x:10,y:20,w:150,h:90}],charts:[]};
  const result=await repairGeometry({folder,output:folder,assembled:folder,index:0,title:'Title',layout,
   data:{fields:{body:{text:'Existing text',evidence:[]}},charts:{}},
   issues:[{key:'body',reason:'overflow',details:['rendered_text_overlap']}],signal:new AbortController().signal,
   textRepair:{keys:['body'],context:{},validate:data=>({data,issues:[]})},
   model:(async(input:any)=>{
    assert.equal(input.payload.requiresFrameRepair,true);
    assert.throws(()=>input.validate({cause:'overlap',action:'shorten',frames:[],fields:[{key:'body',text:'Short',evidence:[]}]}),/changed frame/);
    return input.validate({cause:'overlap',action:'narrow',frames:[{key:'body',x:10,y:20,w:100,h:90}],fields:[]});
   }) as any,
   native:async(_a,_b,request)=>({issues:[],...(request.repairOptions?{geometryOptions:[{fields:[{key:'body',box:{x:10,y:20,w:150,h:90}}]}]}:{})}),
  });
  assert.ok(result);assert.equal(layout.geometryRepairs.body.w,100);
 }finally{await rm(folder,{recursive:true,force:true});}
});
test('partial geometry progress can be refined but repeated refusals and total calls are bounded',()=>{
 assert.equal(geometryAttemptAvailable([{accepted:true},{accepted:false}]),true);
 assert.equal(geometryAttemptAvailable([{accepted:false},{accepted:false}]),false);
 assert.equal(geometryAttemptAvailable([{accepted:true},{accepted:false},{accepted:true},{accepted:true}]),false);
});
test('geometry feedback preserves exact bottom and uses current height for measured shortage',()=>{
 const fields=[{key:'title',box:{x:20,y:118.76,w:130,h:32.11}}];
 const [result]=geometryBounds(fields,{title:{x:20,y:117,w:130,h:34}},[{key:'title',reason:'overflow',measuredHeightPt:18.5,availableHeightPt:17.71} as any]);
 assert.equal(result.originalBounds.bottom,150.87);
 assert.ok(Math.abs(result.suggestedHeightPt!-36.29)<1e-9);
});

test('oversized paragraphs use AI text repair while tiny height deficits retain geometry assistance', async()=>{
 const {geometryRepairKeys}=await import('../src/geometry-repair.js');
 const keys=geometryRepairKeys([
  {key:'long',reason:'overflow',details:['source_text_frame_overflow'],measuredHeightPt:220,availableHeightPt:100,suggestedMaxChars:80},
  {key:'small',reason:'overflow',details:['source_text_frame_overflow'],measuredHeightPt:28,availableHeightPt:27},
  {key:'visual',reason:'overflow',details:['rendered_text_overlap']},
 ] as any,{slots:[{key:'long',role:'body'},{key:'small',role:'title'},{key:'visual',role:'body'}]} as any);
 assert.deepEqual([...keys],['small','visual']);
});

test('exhausted geometry attempts are scoped to the actual layout, not slide number',async()=>{
 const {geometryRepairExhausted}=await import('../src/geometry-repair.js');
 const {digest}=await import('../src/repair.js');
 const {writeJson}=await import('../src/store.js');
 const folder=await mkdtemp(join(tmpdir(),'nerpa-exhausted-'));
 try{
  const layout:any={id:'free',slots:[],charts:[]};
  assert.equal(await geometryRepairExhausted(folder,0,layout),false);
  await writeJson(join(folder,'geometry-repair-0.json'),{identity:digest({version:4,layout:{...layout,geometryRepairs:undefined}}),attempts:[{accepted:false},{accepted:false}]});
  assert.equal(await geometryRepairExhausted(folder,0,layout),true);
  assert.equal(await geometryRepairExhausted(folder,0,{...layout,id:'different'}),false);
 }finally{await rm(folder,{recursive:true,force:true});}
});
