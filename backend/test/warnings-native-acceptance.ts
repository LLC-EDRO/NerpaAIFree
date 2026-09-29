import 'dotenv/config';
import assert from 'node:assert/strict';
import {join,resolve} from 'node:path';
import {mkdir,access} from 'node:fs/promises';
import {native} from '../src/pipeline.js';
import {readJson,writeJson} from '../src/store.js';
import {renderWithRepair} from '../src/repair.js';
import {qualityWarnings} from '../src/quality-warnings.js';
const project=resolve('data/projects/15a9b6b8-c489-4b04-8e11-eabaf332a997');
const assembled=join(project,'assembled-r4');
const root=resolve('../artifacts/warnings-policy-2026-09-24');
await mkdir(root,{recursive:true});
const filled=await readJson(join(project,'filled-r4.json'));
const oversized=structuredClone(filled.slides[2]);
oversized.native.ordinal=1;
oversized.native.fields.r_s6='Проверка длинного заголовка: '.repeat(35);
const original=structuredClone(filled.slides[8]);
original.native.ordinal=2;
const analysis=await readJson(join(assembled,'analysis.json'));
const layout=analysis.layouts.find((l:any)=>l.id===original.native.sourceSlideId);
original.native={sourceSlideId:layout.id,mode:'source',preserveTemplate:true,preserveSource:true,ordinal:2,
 fields:Object.fromEntries(layout.slots.map((s:any)=>[s.key,s.text||''])),charts:{}};
const slides=[oversized,original];
console.log('Native fit: confirm oversized content is detected');
const fit=await native('fit',assembled,{slides});
assert.ok(fit.issues.length);
const output=join(root,'output');await mkdir(output,{recursive:true});
console.log('Export PPTX despite diagnosed fit issues');
const result=await renderWithRepair({output,identity:'real-warning-export',slideCount:2,maxRepairs:0,
 signal:new AbortController().signal,prepare:async()=>({slides}),
 render:async()=>native('export',assembled,{slides,output,allowQualityWarnings:true}),
 notify:async()=>{},onUnresolved:async(result:any,issues)=>{
  assert.equal(result.pptxWritten,true);return {...result,issues:[],warnings:issues};
 }});
assert.equal(result.pptxWritten,true);assert.equal(result.pdfAvailable,true);
assert.ok(result.warnings.length);assert.deepEqual(result.issues,[]);
await access(join(output,'presentation.pptx'));await access(join(output,'presentation.pdf'));
await writeJson(join(root,'acceptance.json'),{fitIssues:fit.issues,warnings:qualityWarnings(result.warnings),pptxWritten:result.pptxWritten,pdfAvailable:result.pdfAvailable});
console.log(JSON.stringify({pptxWritten:true,pdfAvailable:true,warningCount:result.warnings.length,output}));
