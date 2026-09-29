import test from 'node:test';
import assert from 'node:assert/strict';
import {createRendererSession,type WarmRenderer} from '../src/warm-renderer.js';
const delay=(ms:number)=>new Promise(r=>setTimeout(r,ms));
function factory(){let created=0,closed=0;return {get created(){return created;},get closed(){return closed;},start:async()=>{const id=++created;let alive=true;return {name:String(id),input:'/private/'+id,alive:()=>alive,close:async()=>{if(alive)closed++;alive=false;}} satisfies WarmRenderer;}};}
test('render calls within a job are serialized and prewarming creates only one worker',async()=>{
 const f=factory(),s=createRendererSession(f.start);let active=0,max=0;
 await Promise.all([s.warm(),s.warm()]);
 const results=await Promise.all([1,2,3].map(i=>s.run(async w=>{max=Math.max(max,++active);await delay(5);active--;return w.name+':'+i;})));
 assert.equal(max,1);assert.equal(f.created,1);assert.deepEqual(results,['1:1','1:2','1:3']);await s.close();assert.equal(f.closed,1);
 await assert.rejects(s.run(async()=>{}),/closed/);
});
test('different jobs can run concurrently and never share mutable workers',async()=>{
 const f=factory(),a=createRendererSession(f.start),b=createRendererSession(f.start);let active=0,max=0;
 const work=async(w:WarmRenderer)=>{max=Math.max(max,++active);await delay(10);active--;return w.name;};
 const names=await Promise.all([a.run(work),b.run(work)]);assert.equal(new Set(names).size,2);assert.equal(max,2);
 await Promise.all([a.close(),b.close()]);assert.equal(f.closed,2);
});
test('failed native command discards its worker and next request uses fresh state',async()=>{
 const f=factory(),s=createRendererSession(f.start);
 await assert.rejects(s.run(async()=>{throw new Error('conversion failed');}));assert.equal(f.closed,1);
 assert.equal(await s.run(async w=>w.name),'2');await s.close();assert.equal(f.closed,2);
});
test('failed warmup is observed, retry is possible, closing waits for in-flight work',async()=>{
 const f=factory();let attempt=0;const s=createRendererSession(async()=>{if(++attempt===1)throw new Error('failed');return f.start();});
 await assert.rejects(s.warm());await s.warm();let finished=false;
 const pending=s.run(async()=>{await delay(10);finished=true;});await delay(1);await s.close();await pending;
 assert.ok(finished);assert.equal(f.closed,1);
});
