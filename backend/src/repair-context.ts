import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { readJson, writeJson } from './store.js';

/** Revision-scoped artefacts, reused across the final render repair pass. */
export async function repairContext(input: {
  output:string; fingerprint:string; count:number; signal:AbortSignal;
  build:()=>Promise<any>; fallback:()=>Promise<any>;
}) {
  const manifest=join(input.output,'context.json');
  const hashes=async()=>Promise.all(Array.from({length:input.count},async(_,i)=>createHash('sha256').update(await readFile(join(input.output,`slide-${i}.png`))).digest('hex')));
  try {
    const saved=await readJson(manifest);
    if(saved.fingerprint===input.fingerprint && saved.value.blankSlides===input.count && JSON.stringify(saved.hashes)===JSON.stringify(await hashes())) return saved.value;
  } catch { input.signal.throwIfAborted(); }
  let value:any;
  try { value=await input.build(); }
  catch {
    input.signal.throwIfAborted();
    // A failed blank preview must not discard editable content or disable AI.
    return {profiles:await input.fallback(),blankSlides:0};
  }
  await writeJson(manifest,{fingerprint:input.fingerprint,hashes:await hashes(),value});
  return value;
}
