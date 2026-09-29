import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { reviewFinalCopy, noWorseFit } from '../src/final-copy-review.js';

async function fixture() {
  const folder=await mkdtemp(join(tmpdir(),'final-meaning-'));
  const data={fields:{a:{text:'Музыка',evidence:[]},b:{text:'12 инсталляций',evidence:['Свет: 12 инсталляций.']}},charts:{}};
  const slots=['a','b'].map((key,i)=>({key,shapeId:i+1,text:'Sample',role:'body',x:10,y:10+i*60,w:300,h:50,size:16,maxChars:300}));
  const input={folder,output:folder,index:0,topic:'Фестиваль',source:'Музыка: 24 выступления. Свет: 12 инсталляций.',approved:{title:'Программа'},
    slide:{native:{sourceSlideId:'any',fields:{a:'Музыка',b:'12 инсталляций'},charts:{}}},data,
    layout:{id:'any',slots,charts:[]},semantics:{composition:'Card',fields:slots.map(s=>({key:s.key,role:'body',group:'card',intent:'Title with caption',required:true,action:'replace'})),charts:[]},
    pool:[],signal:new AbortController().signal,fit:async(_:any)=>[] as any[]};
  return {input,clean:()=>rm(folder,{recursive:true,force:true})};
}
const response=()=>({issues:[{key:'b',category:'factual_error',claim:'12 инсталляций',sourceQuote:'Музыка: 24 выступления.',message:'Подпись музыки получила показатель света.'}],changes:[{key:'b',text:'24 выступления',evidence:['Музыка: 24 выступления.']}]});

test('chosen warning candidate gets one grounded semantic patch, no geometry change, and a reusable receipt',async()=>{
 const {input,clean}=await fixture();let calls=0;
 const call:any=async(r:any)=>{calls++;return r.validate(response());};
 try {
   const result=await reviewFinalCopy(input,call);
   assert.equal(result.data.fields.b.text,'24 выступления');
   assert.equal(result.slide.native.fields.a,'Музыка');
   assert.deepEqual(input.data.fields.b.text,'12 инсталляций');
   assert.equal(result.warnings[0].reason,'content_connections_repaired');
   await reviewFinalCopy(input,call);assert.equal(calls,1);
   await reviewFinalCopy({...input,approved:{title:'Другая программа'}},call);assert.equal(calls,2);
 } finally {await clean();}
});

test('new overflow or fabricated number cannot replace the existing warning candidate',async()=>{
 for (const mode of ['overflow','number']) {
   const {input,clean}=await fixture();
   try {
     if (mode==='overflow') input.fit=async(slide:any)=>slide.native.fields.b==='24 выступления'?[{key:'b',reason:'overflow'}]:[];
     const result=await reviewFinalCopy(input,async(r:any)=>{const raw=response();if(mode==='number')raw.changes[0].text='999 выступлений';return r.validate(raw);});
     assert.equal(result.data.fields.b.text,'12 инсталляций');assert.ok(result.warnings.some((w:any)=>w.reason==='content_mismatch'));
   } finally {await clean();}
 }
});

test('provider failure and changes without a grounded issue remain warnings; protected fields cannot change',async()=>{
 for(const mode of ['provider','unanchored','protected']) {
   const {input,clean}=await fixture();
   try {
     if(mode==='protected')input.semantics.fields[1].action='preserve';
     const result=await reviewFinalCopy(input,async(r:any)=>{if(mode==='provider')throw Error('offline');const raw=response();if(mode==='unanchored')raw.issues[0].claim='Absent';return r.validate(raw);});
     assert.equal(result.reviewed,false);assert.deepEqual(result.data,input.data);assert.equal(result.warnings[0].reason,'content_review_unavailable');
   } finally {await clean();}
 }
});

test('fit comparison rejects increased measured overflow even with unchanged issue keys',()=>{
 const old=[{key:'x',reason:'overflow',measuredHeightPt:60}];
 assert.equal(noWorseFit(old,[{...old[0],measuredHeightPt:59}]),true);
 assert.equal(noWorseFit(old,[{...old[0],measuredHeightPt:70}]),false);
});
