import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { recoverSlideWithWarnings } from '../src/slide-fallback.js';
test('later bad geometry cannot discard an earlier filled candidate for source sample text',async()=>{
 const output=await mkdtemp(join(tmpdir(),'fallback-'));
 const data={fields:{r_title:{text:'New subject',evidence:[]}},charts:{}};
 const attempts=[{scene:{id:'good'},data,issues:[]},...['bad-one','bad-two'].map(id=>({scene:{id},data,issues:[{reason:'overflow'}]}))];
 try{
  await writeFile(join(output,'rebuild-0.json'),JSON.stringify({attempts}));
  const seen:string[]=[];
  const result=await recoverSlideWithWarnings({output,assembled:output,index:0,layout:{id:'source',slots:[{key:'s_original',text:'Old sample'}],charts:[]},title:'New',issues:[],signal:new AbortController().signal,
    native:async(_a,_f,p)=>{seen.push(p.slides[0].native.rebuild?.id);if(p.slides[0].native.rebuild?.id!=='good')throw Error('invalid');return{issues:[]};}});
  assert.equal(result.slide.native.fields.r_title,'New subject');assert.deepEqual(seen,['good']);
 } finally {await rm(output,{recursive:true,force:true});}
});
