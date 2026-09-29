import 'dotenv/config';
import assert from 'node:assert/strict';
import {join,resolve} from 'node:path';
import {llmJson} from '../src/llm.js';
import {contentReviewPrompt,parseContentReview} from '../src/content-review.js';
import {writeJson} from '../src/store.js';

// Explicitly invoked paid smoke, excluded from the ordinary test suite.
// Synthetic facts supplied as materials; no external factual claims or search.
const folder=resolve('data/qa-policy-live',String(Date.now()));
const source='В обследованных организациях за 2024 год установлено 600 промышленных роботов.';
const cases=[
 {name:'paraphrase',text:'Промышленная роботизация: 600 установок за 2024 год',blocks:false},
 {name:'contradiction',text:'В обследованных организациях в 2024 году установлено 800 промышленных роботов.',blocks:true},
];
const results=await Promise.all(cases.map(async item=>{
 const data={fields:{caption:{text:item.text,evidence:['fixture-fact']}},charts:{}};
 const started=Date.now();
 const result=await llmJson({folder,stage:`content-review-smoke-${item.name}`,prompt:contentReviewPrompt,
  payload:{topic:'Промышленная роботизация',userSource:source,approved:{title:'Роботизация',brief:'Краткий итог исследования'},data,
   semantics:{fields:[{key:'caption',role:'body',action:'replace'}],charts:[]}},
  validate:raw=>parseContentReview(raw,data,source),
 });
 return {name:item.name,text:item.text,expectedBlocking:item.blocks,seconds:(Date.now()-started)/1000,...result};
}));
await writeJson(join(folder,'results.json'),results);
for(const item of results)assert.equal(item.issues.length>0,item.expectedBlocking,item.name);
console.log(JSON.stringify({folder,results},null,2));
