import test from 'node:test';import assert from 'node:assert/strict';import {AsyncLocalStorage} from 'node:async_hooks';
import {createNativePool} from '../src/native-pool.js';
test('native previews leave capacity for fit/export, queued jobs retain their owner context',async()=>{
  const pool=createNativePool(2),contexts=new AsyncLocalStorage<string>(),order:string[]=[];
  let release!:()=>void;const hold=new Promise<void>(r=>release=r);
  const p1=contexts.run('first',()=>pool(async()=>{order.push(contexts.getStore()!);await hold},true));
  const p2=contexts.run('second',()=>pool(async()=>{order.push(contexts.getStore()!)},true));
  const fit=contexts.run('fit',()=>pool(async()=>{order.push(contexts.getStore()!)}));
  await fit;assert.deepEqual(order,['first','fit']);release();await Promise.all([p1,p2]);assert.deepEqual(order,['first','fit','second']);
});
test('independent previews use spare capacity while reserving a worker for fitting',async()=>{
  const pool=createNativePool(3);let release!:()=>void;const hold=new Promise<void>(r=>release=r);let previews=0;
  const pending=[0,1,2].map(()=>pool(async()=>{previews++;await hold},true));
  await pool(async()=>{assert.equal(previews,2)});
  release();await Promise.all(pending);assert.equal(previews,3);
});

test('continuous fitting cannot indefinitely starve an aged preview',async()=>{
 let now=0,release!:()=>void;const order:string[]=[];
 const pool=createNativePool(1,{now:()=>now,maxPreviewWaitMs:50});
 const first=pool(async()=>{await new Promise<void>(r=>release=r)});
 await Promise.resolve();
 const preview=pool(async()=>{order.push('preview')},true);
 const fit=pool(async()=>{order.push('fit')});
 now=51;release();await Promise.all([first,preview,fit]);
 assert.deepEqual(order,['preview','fit']);
});
