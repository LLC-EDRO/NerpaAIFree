import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { repairContext } from '../src/repair-context.js';
import { sandboxArtifactAllowed, validateSandboxArtifacts } from '../src/sandbox-protocol.js';

test('repair visual cache is revision scoped and rejects altered images',async()=>{
 const output=await mkdtemp(join(tmpdir(),'repair-context-'));let calls=0;
 const args={output,fingerprint:'version-one',count:1,signal:new AbortController().signal,
  build:async()=>{calls++;await writeFile(join(output,'slide-0.png'),'image');return{profiles:{a:{}},blankSlides:1};},fallback:async()=>({})};
 try{
  await repairContext(args);await repairContext(args);assert.equal(calls,1);
  await writeFile(join(output,'slide-0.png'),'altered');await repairContext(args);assert.equal(calls,2);
  await repairContext({...args,fingerprint:'version-two'});assert.equal(calls,3);
  const degraded=await repairContext({...args,fingerprint:'unavailable',build:async()=>{throw Error('render');},fallback:async()=>({source:{}})});
  assert.deepEqual(degraded,{profiles:{source:{}},blankSlides:0});
 }finally{await rm(output,{recursive:true,force:true});}
});
test('blank previews retain sandbox path and completeness boundaries',()=>{
 assert.equal(sandboxArtifactAllowed('repair_context','slide-12.png'),true);
 for(const path of ['../slide-0.png','blank.pptx','source.pptx'])assert.equal(sandboxArtifactAllowed('repair_context',path),false);
 assert.throws(()=>validateSandboxArtifacts('repair_context',{blankSlides:2,profiles:{a:{},b:{}}},['slide-0.png'],0));
 validateSandboxArtifacts('repair_context',{blankSlides:1,profiles:{a:{}}},['slide-0.png'],0);
});
