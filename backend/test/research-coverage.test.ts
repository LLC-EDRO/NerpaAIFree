import test from 'node:test';
import assert from 'node:assert/strict';
import {validateResearchCoverage} from '../src/research-coverage.js';
test('coverage cannot suppress research with unknown IDs, missing slides or unsourced numbers',()=>{
 const facts=[{id:'f',text:'42% surveyed enterprises in 2024'}], numeric=new Set([2]);
 const valid={slides:[{slide:1,evidenceIds:[],missing:''},{slide:2,evidenceIds:['f'],missing:''}]};
 assert.equal(validateResearchCoverage(valid,2,numeric,facts).length,2);
 for(const bad of [
  {slides:[valid.slides[0]]},
  {slides:[valid.slides[0],valid.slides[0]]},
  {slides:[valid.slides[0],{slide:2,evidenceIds:['invented'],missing:''}]},
  {slides:[valid.slides[0],{slide:2,evidenceIds:[],missing:''}]},
 ]) assert.throws(()=>validateResearchCoverage(bad,2,numeric,facts));
 assert.equal(validateResearchCoverage({slides:[valid.slides[0],{slide:2,evidenceIds:[],missing:'Find a statistic for this subject'}]},2,numeric,facts)[1].missing.length>0,true);
});
