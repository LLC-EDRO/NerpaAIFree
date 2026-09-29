import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,stat,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';

test('Model selection persists privately, routes local calls and isolates experiment settings',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'nerpa-model-settings-'));
 process.env.DATA_DIR=join(folder,'projects');process.env.OPENAI_API_KEY='test-cloud-secret';
 const settings=await import('../src/model-settings.js');
 const {llmJson,modelInfo}=await import('../src/llm.js');
 const calls:Array<{url:string;authorization?:string;body:any}>=[];
 const server=createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  calls.push({url:req.url!,authorization:req.headers.authorization,body:raw?JSON.parse(raw):undefined});
  res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify(req.url==='/api/models'?{data:[{id:'local-text',name:'Text model'},{id:'local-vision'}]}:{model:'local-text',choices:[{message:{content:'{"ok":true}'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:4}}));
 });
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const baseUrl=`http://127.0.0.1:${(server.address() as {port:number}).port}`;
 const config={provider:'openwebui',baseUrl,textModel:'local-text',visionModel:'local-vision',apiKey:'private-local-key'};
 try{
  const saved=await settings.saveModelSettings(config);
  assert.equal(saved.keyConfigured,true);assert.equal('apiKey' in saved,false);
  assert.equal((await stat(join(folder,'model-settings.json'))).mode & 0o777,0o600);
  await settings.loadModelSettings();assert.equal(settings.currentSelection().textModel,'local-text');
  await settings.withModelSelection(settings.currentSelection(),()=>llmJson({stage:'fill-test',folder,prompt:'JSON',payload:{a:1},schema:{type:'object'},validate:v=>v}));
  const chat=calls.find(c=>c.url==='/api/chat/completions')!;
  assert.equal(chat.authorization,'Bearer private-local-key');assert.equal(chat.body.model,'local-text');
  assert.equal(chat.body.response_format.type,'json_schema');assert.equal(chat.body.stream,true);
  assert.deepEqual(chat.body.response_format.json_schema.schema,{type:'object'});
  assert.equal(chat.body.params.max_tokens,chat.body.max_tokens);
  assert.equal(chat.body.stream_options.include_usage,true);
  assert.equal('reasoning_effort' in chat.body,false);assert.equal('max_completion_tokens' in chat.body,false);
  const snapshot=settings.currentSelection();assert.equal('apiKey' in snapshot,false);
  const values=await Promise.all([
   settings.withModelSelection(snapshot,async()=>{await new Promise(r=>setTimeout(r,10));return modelInfo().textModel;}),
   settings.withModelSelection(settings.defaultSelection(),async()=>modelInfo().textModel),
  ]);assert.equal(values[0],'local-text');assert.notEqual(values[1],'local-text');
  let fallback:any;
  await settings.withModelSelection({...snapshot,visionModel:''},()=>llmJson({stage:'describe-test',folder,prompt:'JSON',payload:{},useVisionModel:true,validate:v=>v,fetcher:async(url,init)=>{
   fallback={url,headers:init?.headers,body:JSON.parse(String(init?.body))};return new Response(JSON.stringify({choices:[{message:{content:'{}'},finish_reason:'stop'}]}));
  }}));
  assert.equal(fallback.url,'https://api.openai.com/v1/chat/completions');assert.notEqual(fallback.body.model,'local-text');
  await assert.rejects(()=>settings.saveModelSettings({...config,textModel:'missing'}),/отсутствует/);
  const before=calls.length;
  await settings.discoverModels({...config,baseUrl:baseUrl.replace('127.0.0.1','localhost'),apiKey:undefined},async(_url,init)=>{
   assert.deepEqual(init?.headers,{});return new Response(JSON.stringify({data:[]}));
  });assert.equal(calls.length,before);
  await assert.rejects(()=>settings.discoverModels(config,async()=>new Response('{}',{status:401})),/API-ключ/);
  await assert.rejects(()=>settings.discoverModels({...config,baseUrl:'https://user:password@server.test'}));
  await settings.saveModelSettings({...config,provider:'default',apiKey:undefined,clearApiKey:true});
  assert.equal(settings.publicModelSettings().keyConfigured,false);
  assert.equal(JSON.parse(await readFile(join(folder,'model-settings.json'),'utf8')).apiKey,'');
 }finally{await new Promise<void>((r,j)=>server.close(e=>e?j(e):r()));await rm(folder,{recursive:true,force:true});}
});
