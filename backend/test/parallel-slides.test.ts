import test from 'node:test';
import assert from 'node:assert/strict';
import { parallelSlides } from '../src/parallel-slides.js';
import { createFitBatch } from '../src/fit-batch.js';
const wait = (ms:number) => new Promise(r => setTimeout(r, ms));
test('bounded slide work preserves ordering and drains failures before releasing job', async () => {
  let active=0, peak=0; const results:number[]=[];
  await parallelSlides([30,5,10,1], new AbortController().signal, async(ms,i)=>{
    peak=Math.max(peak,++active); await wait(ms); results[i]=i; active--;
  },2);
  assert.equal(peak,2); assert.deepEqual(results,[0,1,2,3]);
  let lateWrite=false, queued=false;
  await assert.rejects(parallelSlides([0,1,2], new AbortController().signal, async(_,i)=>{
    if(i===0){await wait(5); throw new Error('first failure');}
    if(i===1){await wait(20); lateWrite=true;} else queued=true;
  },2),/first failure/);
  assert.equal(lateWrite,true); assert.equal(queued,false);
});
test('fit batches retain slide-specific errors and one-based expansion reports', async()=>{
  let calls=0;
  const batch=createFitBatch(async(_a,_f,e)=>{
    calls++; assert.deepEqual(e.slides,['first','second']);
    return {issues:[{slide:1,key:'same-key',reason:'overflow'}],fieldChanges:[{slide:1,key:'same-key'},{slide:2,key:'other'}]};
  },1);
  const signal=new AbortController().signal;
  const [a,b]=await Promise.all([batch('/a','first',signal),batch('/a','second',signal)]);
  assert.equal(calls,1); assert.deepEqual(a.issues,[]);assert.equal(b.issues[0].slide,0);
  assert.equal(a.fieldChanges[0].key,'same-key'); assert.equal(b.fieldChanges[0].slide,1);
});
test('fit rejects unknown issue routing and isolates cancellation signals', async()=>{
  const batch=createFitBatch(async()=>({issues:[{slide:9}]}),1);
  await assert.rejects(batch('/a',{}),/invalid_native_batch_result/);
  const c=new AbortController();c.abort(new Error('cancelled'));
  let calls=0; const isolated=createFitBatch(async()=>{calls++;return{issues:[]};},1);
  const [a,b]=await Promise.allSettled([isolated('/a',{},c.signal),isolated('/a',{})]);
  assert.equal(a.status,'rejected');assert.equal(b.status,'fulfilled');assert.equal(calls,1);
});
test('fit requests keep coalescing while the native worker is occupied',async()=>{
 let release:()=>void=()=>{};const gate=new Promise<void>(r=>release=r);let calls=0;
 const batch=createFitBatch(async(_a,_f,e)=>{calls++;assert.equal(e.slides.length,3);return{issues:[]};},1,async task=>{await gate;await task();});
 const a=batch('/busy','a');await wait(5);
 const b=batch('/busy','b'),c=batch('/busy','c');release();
 await Promise.all([a,b,c]);assert.equal(calls,1);
});

test('one malformed slide does not reject valid siblings in the same sandbox batch',async()=>{
 const batch=createFitBatch(async()=>({issues:[],slideErrors:[{slide:1,code:'pptx_fields_mismatch'}]}),1);
 const result=await Promise.allSettled([batch('/same','valid'),batch('/same','bad')]);
 assert.equal(result[0].status,'fulfilled');assert.equal(result[1].status,'rejected');
 if(result[1].status==='rejected')assert.equal(result[1].reason.code,'pptx_fields_mismatch');
});
