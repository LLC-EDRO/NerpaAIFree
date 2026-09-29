import {spawn,execFile,type ChildProcess} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,mkdir,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
const exec=promisify(execFile);

export interface WarmRenderer {name:string;input:string;close:()=>Promise<void>;alive:()=>boolean}
export async function startWarmRenderer(binary:string,args:(name:string,input:string)=>string[],prepare?:(input:string)=>Promise<void>):Promise<WarmRenderer>{
  const work=await mkdtemp(join(tmpdir(),'nerpa-render-worker-')),input=join(work,'input'),name='nerpa-render-'+randomUUID();
  await mkdir(input,{mode:0o700});
  let child:ChildProcess|undefined,ended=false,closing:Promise<void>|undefined;
  const close=()=>closing ||= (async()=>{
    ended=true;
    await exec(binary,['rm','--force',name],{timeout:10000,maxBuffer:4096}).catch(()=>{});
    child?.kill('SIGKILL');await rm(work,{recursive:true,force:true});
  })();
  try{
    await prepare?.(input);
    const command=args(name,input);
    command.splice(command.length-1,0,'--env','PRESENTATION_WARM_RENDERER=1','--entrypoint','timeout');
    command.push('--signal=KILL','900','python','-I','/opt/pptx/render_worker.py');
    child=spawn(binary,command,{stdio:['ignore','pipe','pipe']});
    child.stderr!.resume();
    await new Promise<void>((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('pptx_renderer_warmup_timeout')),45000);
      let text='';
      const fail=()=>{ended=true;clearTimeout(timer);reject(new Error('pptx_renderer_warmup_failed'));};
      child!.once('error',fail);child!.once('close',fail);
      child!.stdout!.on('data',(chunk:Buffer)=>{
        text+=chunk.toString('utf8');
        if(text.length>4096){clearTimeout(timer);reject(new Error('pptx_renderer_warmup_failed'));return;}
        if(text.includes('\n')){
          try{if(JSON.parse(text.trim()).ready!==true)throw new Error();clearTimeout(timer);resolve();}
          catch{clearTimeout(timer);reject(new Error('pptx_renderer_warmup_failed'));}
        }
      });
    });
    return {name,input,close,alive:()=>!ended};
  }catch(error){await close();throw error;}
}

/** One converter per job, serial commands within it. Other jobs have separate
 * containers/profiles. A failed command discards the worker before reuse. */
export function createRendererSession(start:()=>Promise<WarmRenderer>){
  let worker:Promise<WarmRenderer>|undefined,tail=Promise.resolve(),stopped=false;
  const ready=()=>{
    if(stopped)return Promise.reject(new Error('pptx_renderer_closed'));
    if(!worker){worker=start();void worker.catch(()=>{worker=undefined;});}
    return worker;
  };
  return {
    warm:async()=>{await ready();},
    run<T>(work:(worker:WarmRenderer)=>Promise<T>):Promise<T>{
      const result=tail.then(async()=>{
        let current=await ready();
        if(!current.alive()){await current.close();worker=undefined;current=await ready();}
        try{return await work(current);}
        catch(error){worker=undefined;await current.close();throw error;}
      });
      tail=result.then(()=>{},()=>{});return result;
    },
    async close(){stopped=true;await tail;await worker?.then(w=>w.close()).catch(()=>{});worker=undefined;},
  };
}
