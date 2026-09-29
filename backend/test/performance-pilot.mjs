import fs from 'node:fs/promises';
import path from 'node:path';
const [sourceFile, topic, destination, rawCount = '15'] = process.argv.slice(2);
if (!sourceFile || !topic || !destination) throw new Error('Usage: node test/performance-pilot.mjs template.pptx "Topic" output-directory [slide-count]');
const count = Number(rawCount);
if (!Number.isInteger(count) || count < 1 || count > 20) throw new Error('Slide count must be 1..20');
const out = path.resolve(destination);
await fs.mkdir(out,{recursive:true});
await fs.writeFile(path.join(out,'run.json'),'{}',{flag:'wx'});
await fs.mkdir(out,{recursive:true});
const base='http://127.0.0.1:4100/api';
const file=path.resolve(sourceFile);
const run={file,topic,count,targetSeconds:300,startedAt:new Date().toISOString(),stages:[],images:'keep-original',freshProject:true};
const save=()=>fs.writeFile(path.join(out,'run.json'),JSON.stringify(run,null,2));
async function api(route,method='GET',body){
 const res=await fetch(base+route,{method,headers:body instanceof FormData?{}:body?{'Content-Type':'application/json'}:{},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});
 const data=await res.json();if(!res.ok)throw new Error(JSON.stringify({route,status:res.status,data}));return data;
}
async function stage(name,launch){
 const started=performance.now();let p=await launch();run.id=p.id;await save();let last='';
 while(p.busy){if(last!==p.message){console.log(JSON.stringify({name,message:p.message,elapsed:(performance.now()-started)/1000}));last=p.message;} await new Promise(r=>setTimeout(r,500));p=await api('/projects/'+p.id);}
 run.stages.push({name,seconds:(performance.now()-started)/1000,status:p.status});await save();
 await fs.writeFile(path.join(out,name+'.json'),JSON.stringify(p,null,2));
 if(p.error||p.status==='error')throw new Error(JSON.stringify(p.error));return p;
}
if((await api('/projects')).some(p=>p.busy))throw new Error('User generation is still running');
const start=performance.now();
try{
 const form=new FormData();form.append('file',new Blob([await fs.readFile(file)]),path.basename(file));
 let p=await stage('upload-and-analysis',()=>api('/projects','POST',form));
 p=await stage('research-and-plan',()=>api('/projects/'+p.id+'/plan','POST',{topic,count,webSearch:true,sourceText:'Презентация на русском языке. Используй различные подходящие макеты. Нужны подтверждённые факты и статистика, понятные сравнения и практические рекомендации по внедрению. Не приписывай университету проведённые мероприятия и достижения. Сохрани исходные изображения.'}));
 p=await stage('assemble-and-description',()=>api('/projects/'+p.id+'/assemble','POST',{revision:p.revision}));
 for(const c of p.visuals?.choices||[])if(c.mode!=='keep')p=await api('/projects/'+p.id+'/visuals','PUT',{revision:p.revision,slideIndex:c.slideIndex,slotIndex:c.slotIndex,mode:'keep',kind:c.kind,background:c.background,instruction:c.instruction||'',quality:c.quality});
 p=await stage('fill-and-export',()=>api('/projects/'+p.id+'/fill','POST',{}));
 for(const ext of ['pptx','pdf']){const r=await fetch(base+'/projects/'+p.id+'/files/output/presentation.'+ext);if(!r.ok)throw new Error('Download failed');await fs.writeFile(path.join(out,'presentation.'+ext),Buffer.from(await r.arrayBuffer()));}
 run.status=p.status;run.tokenUsage=p.tokenUsage;run.result=p.result;
}catch(e){run.status='error';run.error=String(e);console.error(run.error);}
run.seconds=(performance.now()-start)/1000;run.withinTarget=run.status==='complete'&&run.seconds<=300;run.finishedAt=new Date().toISOString();await save();console.log(JSON.stringify(run));
