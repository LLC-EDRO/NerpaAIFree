import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,stat,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:http';

test('Gemma 4 settings discover Ollama locally and route text and vision without credentials',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'nerpa-model-settings-'));
 process.env.DATA_DIR=join(folder,'projects');
 const settings=await import('../src/model-settings.js');
 const {llmJson}=await import('../src/llm.js');
 const calls:Array<{url:string;authorization?:string;body:any}>=[];
 const server=createServer(async(req,res)=>{
  let raw='';for await(const chunk of req)raw+=chunk;
  calls.push({url:req.url!,authorization:req.headers.authorization,body:raw?JSON.parse(raw):undefined});
  res.setHeader('Content-Type','application/json');
  res.end(JSON.stringify(req.url==='/api/tags'?{models:[{name:'gemma4:12b',details:{parameter_size:'12B'}},{name:'gemma2:latest'}]}:{model:'gemma4:12b',choices:[{message:{content:'{"ok":true}'},finish_reason:'stop'}],usage:{prompt_tokens:10,completion_tokens:4}}));
 });
 await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));
 const baseUrl=`http://127.0.0.1:${(server.address() as {port:number}).port}`;
 const config={provider:'ollama',baseUrl,textModel:'gemma4:12b',visionModel:'gemma4:12b'};
 try{
  const models=await settings.discoverModels(config);assert.deepEqual(models.map(m=>m.id),['gemma4:12b']);
  const saved=await settings.saveModelSettings(config);
  assert.equal(saved.keyConfigured,false);assert.equal(saved.imageModel,'FLUX.2-klein-4B');
  assert.equal((await stat(join(folder,'model-settings.json'))).mode & 0o777,0o600);
  assert.equal(JSON.parse(await readFile(join(folder,'model-settings.json'),'utf8')).apiKey,'');
  await settings.loadModelSettings();assert.equal(settings.currentSelection().textModel,'gemma4:12b');
  await settings.withModelSelection(settings.currentSelection(),()=>llmJson({stage:'fill-test',folder,prompt:'JSON',payload:{a:1},schema:{type:'object'},validate:v=>v}));
  const chat=calls.find(c=>c.url==='/v1/chat/completions')!;
  assert.equal(chat.authorization,undefined);assert.equal(chat.body.model,'gemma4:12b');
  assert.equal(chat.body.response_format.type,'json_schema');assert.equal(chat.body.stream,true);
  assert.deepEqual(chat.body.response_format.json_schema.schema,{type:'object'});
  await assert.rejects(()=>settings.saveModelSettings({...config,textModel:'missing'}),/Установите gemma4:12b/);
  await assert.rejects(()=>settings.discoverModels({...config,baseUrl:'https://remote.example'}));
 }finally{await new Promise<void>((r,j)=>server.close(e=>e?j(e):r()));await rm(folder,{recursive:true,force:true});}
});
