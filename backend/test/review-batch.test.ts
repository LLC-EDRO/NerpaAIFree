import test from 'node:test';import assert from 'node:assert/strict';
import {createReviewBatch} from '../src/review-batch.js';
test('reviews share source context while returning independent issues for identical field keys',async()=>{
 let calls=0;
 const run=createReviewBatch((async(input:any)=>{
  calls++;assert.equal(input.payload.researchEvidence.length,1);assert.equal(input.payload.slides.length,2);
  return input.validate({slides:[{slide:2,issues:['second']},{slide:1,issues:[]}]});
 }) as any,1);
 const signal=new AbortController().signal;
 const request={folder:'/test',stage:'content-review-1',signal,prompt:'check',payload:{topic:'test',userSource:'',researchEvidence:[{id:'a',text:'fact'}],data:{fields:{same:'value'}}},validate:(v:any)=>v.issues};
 const [a,b]=await Promise.all([run(request),run(request)]);assert.equal(calls,1);assert.deepEqual(a,[]);assert.deepEqual(b,['second']);
});
test('partial review response rejects every dependent slide rather than passing unchecked content',async()=>{
 const run=createReviewBatch((async(input:any)=>input.validate({slides:[{slide:1,issues:[]}]})) as any,1);
 const request={folder:'/test',stage:'content-review',prompt:'check',payload:{topic:'t',userSource:''},validate:(v:any)=>v};
 const results=await Promise.allSettled([run(request),run(request)]);assert.ok(results.every(r=>r.status==='rejected'));
});
