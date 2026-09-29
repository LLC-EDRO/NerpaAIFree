import test from 'node:test';
import assert from 'node:assert/strict';
import { reviewRebuild, identicalRebuildAttempt } from '../src/rebuild-review.js';

test('geometry repair reuses only the same semantic review and context',async()=>{
  const state:any={};let calls=0;
  const call:any=async()=>{calls++;return {issues:[]};};
  const input:any={folder:'/project',prompt:'review',payload:{topic:'t',userSource:'source',approved:{title:'t'},data:{fields:{x:{text:'20',evidence:['a']}}},semantics:{fields:[{key:'x',group:'a'}]},tableCells:[{key:'x',table:1,cell:[1,0]}]},validate:(v:any)=>v};
  await reviewRebuild(state,input,call);
  await reviewRebuild(state,{...input,payload:structuredClone(input.payload)},call);
  assert.equal(calls,1);
  for(const change of [
    (p:any)=>p.userSource='updated',
    (p:any)=>p.data.fields.x.text='21',
    (p:any)=>p.data.fields.x.evidence=['b'],
    (p:any)=>p.semantics.fields[0].group='b',
    (p:any)=>p.tableCells[0].cell=[1,1],
    (p:any)=>p.approved.title='different',
  ]) {const payload=structuredClone(input.payload);change(payload);await reviewRebuild(state,{...input,payload},call);}
  assert.equal(calls,7);
  const aborted=new AbortController();aborted.abort();
  await assert.rejects(reviewRebuild(state,{...input,signal:aborted.signal},call));
});

test('provider errors do not become review receipts',async()=>{
  const state:any={};let calls=0;
  const request:any={payload:{},prompt:'p',validate:(v:any)=>v};
  const call:any=async()=>{calls++;if(calls===1)throw new Error('provider');return {issues:[]};};
  assert.deepEqual(await reviewRebuild(state,request,call),{issues:[],unavailable:true});
  assert.equal(state.contentReviews,undefined);
  await reviewRebuild(state,request,call);assert.equal(calls,2);
});

test('no-progress guard stops exact duplicates but allows new geometry or content',()=>{
  const scene={texts:[{key:'a',x:10,w:100}]},data={fields:{a:{text:'Example'}}};
  const attempts=[{scene,data,issues:[{key:'a',reason:'overflow'}]}];
  assert.ok(identicalRebuildAttempt(attempts,structuredClone(scene),structuredClone(data)));
  assert.equal(identicalRebuildAttempt(attempts,{texts:[{key:'a',x:10,w:120}]},data),undefined);
  assert.equal(identicalRebuildAttempt(attempts,scene,{fields:{a:{text:'Short'}}}),undefined);
});
