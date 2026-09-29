import 'dotenv/config';
import { mkdir, writeFile, readdir, readFile } from 'node:fs/promises';
import { resolve,join } from 'node:path';
import { llmJson, modelInfo } from '../src/llm.js';
import {providerJson} from '../src/provider-request.js';
const folder=resolve('data/migration-gpt6-luna',new Date().toISOString().replace(/[:.]/g,'-'));
await mkdir(folder,{recursive:true});
const results=await Promise.allSettled([
  {stage:'fill-migration-low',prompt:'Верни JSON {title,metrics:[{value,label}]}. Сохрани ровно четыре показателя с единицами из source.',payload:{source:'Учебный пилот: 12480 обращений, 6 районов, 42 оператора, 86% в срок.'},validate:(v:any)=>{if(v.metrics?.length!==4 || !v.title)throw Error('four metrics required');return v;}},
  {stage:'describe-migration-low',prompt:'По изображению верни JSON {title,columns:number,rowsIncludingHeader:number}. Посчитай строки и колонки таблицы.',payload:{task:'native table structure'},images:[resolve('data/projects/c6f7debe-1a59-4490-a81c-bfc37c51b8df/output-r4/slide-3.png')],validate:(v:any)=>{if(v.columns!==4||v.rowsIncludingHeader!==7)throw Error('incorrect table structure');return v;}},
  {stage:'fill-geometry-migration-medium',reasoning:'medium' as const,prompt:'Предложи JSON {x,y,w,h}. Расширь текстовую рамку только в свободную область страницы. Сохрани x,y, учти соседний объект, шрифт менять нельзя. Высота уже подходит.',payload:{current:{x:40,y:50,w:80,h:40},measuredWidth:130,page:{w:720,h:405},neighbor:{x:220,y:50,w:120,h:40}},validate:(v:any)=>{if(v.x!==40||v.y!==50||v.w<130||v.x+v.w>220||v.h!==40)throw Error('invalid repair geometry');return v;}},
].map(async task=>{const start=Date.now();const result=await llmJson({folder,...task});return {stage:task.stage,seconds:(Date.now()-start)/1000,result};}));
const start=Date.now();
let search:any;
try {
 const response=await providerJson({url:'https://api.openai.com/v1/responses',prefix:'search',model:modelInfo().searchModel,timeoutMs:60000,init:{method:'POST',headers:{Authorization:`Bearer ${process.env.OPENAI_API_KEY}`,'Content-Type':'application/json'},body:JSON.stringify({model:modelInfo().searchModel,store:false,reasoning:{effort:'low'},max_output_tokens:800,max_tool_calls:1,tools:[{type:'web_search'}],tool_choice:'required',input:'Найди официальную страницу OpenAI с моделью GPT-6 Luna. Ответь одной фразой со ссылкой.'})}});
 search={status:response.status,model:response.model,seconds:(Date.now()-start)/1000,webSearchCalls:response.output?.filter((o:any)=>o.type==='web_search_call').length,usage:response.usage};
 if(search.status!=='completed'||!search.webSearchCalls)throw Error('search incomplete');
 await writeFile(join(folder,'search-response.json'),JSON.stringify(response,null,2));
}catch(e){search={error:e instanceof Error?e.message:String(e)}}
const receipts=await Promise.all((await readdir(join(folder,'llm'))).map(async name=>{const r=JSON.parse(await readFile(join(folder,'llm',name),'utf8'));return {stage:r.stage,model:r.model,responseModel:r.responseModel,reasoningEffort:r.reasoningEffort,durationMs:r.durationMs,usage:r.usage};}));
const report={models:modelInfo(),results:results.map(r=>r.status==='fulfilled'?r.value:{error:String(r.reason)}),search,receipts};
await writeFile(join(folder,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({folder,...report},null,2));
if(results.some(r=>r.status==='rejected')||search.error)process.exitCode=1;
