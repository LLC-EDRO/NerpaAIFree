import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSharedSnapshot} from '../src/shared-snapshot.js';
import {fontSnapshotKey,sandboxInputFiles} from '../src/sandbox.js';
import {sandboxArtifactAllowed,validateSandboxArtifacts} from '../src/sandbox-protocol.js';

test('concurrent users share immutable preparation and disposal waits for every user',async()=>{
  const disposed:number[]=[];let builds=0;
  const get=createSharedSnapshot(async value=>{disposed.push(value as number);},10);
  const make=async()=>++builds;
  const [a,b]=await Promise.all([get('same',make),get('same',make)]);
  assert.equal(builds,1);assert.equal(a.value,b.value);
  a.release();a.release();await new Promise(r=>setTimeout(r,20));assert.deepEqual(disposed,[]);
  const c=await get('changed-fonts',make);assert.equal(c.value,2);
  b.release();await new Promise(r=>setTimeout(r,20));assert.deepEqual(disposed,[1]);
  c.release();await new Promise(r=>setTimeout(r,20));assert.deepEqual(disposed,[1,2]);
});
test('failed preparation can be retried and never becomes a successful cache entry',async()=>{
  const get=createSharedSnapshot(async()=>{},5);
  await assert.rejects(get('a',async()=>{throw new Error('failed');}));
  const next=await get('a',async()=>42);assert.equal(next.value,42);next.release();
});
test('font and sandbox changes invalidate the snapshot; unrelated files do not enter it',async()=>{
  const folder=await mkdtemp(join(tmpdir(),'font-key-test-'));
  try{
    const mounts=[{source:folder,target:'/fonts/open'}];await writeFile(join(folder,'face.ttf'),'abc');
    const first=await fontSnapshotKey(mounts,'image-a');
    await writeFile(join(folder,'secret.txt'),'excluded');assert.equal(await fontSnapshotKey(mounts,'image-a'),first);
    await writeFile(join(folder,'face.ttf'),'changed');assert.notEqual(await fontSnapshotKey(mounts,'image-a'),first);
    assert.notEqual(await fontSnapshotKey(mounts,'image-a'),await fontSnapshotKey(mounts,'image-b'));
  }finally{await rm(folder,{recursive:true,force:true});}
});
test('font cache preparation takes no uploaded files and has a strict output allowlist',()=>{
  assert.deepEqual(sandboxInputFiles('font_cache',true),[]);
  const cache='fontconfig/'+'a'.repeat(32)+'-le64.cache-8';
  assert.ok(sandboxArtifactAllowed('font_cache',cache));
  for(const file of ['source.pptx','analysis.json','../font-index.json','fontconfig/../../.env','fontconfig/script.py'])assert.equal(sandboxArtifactAllowed('font_cache',file),false);
  assert.throws(()=>validateSandboxArtifacts('font_cache',{fonts:10},['font-index.json'],0));
  assert.doesNotThrow(()=>validateSandboxArtifacts('font_cache',{fonts:10},['font-index.json',cache],0));
});
