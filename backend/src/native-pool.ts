import {AsyncResource} from 'node:async_hooks';

/** Prefer filling/export, but do not starve the first visible result while a
 * stream of fit requests keeps arriving. At least one slot remains for fitting. */
export function createNativePool(limit:number, options:{now?:()=>number;maxPreviewWaitMs?:number}={}) {
  const now=options.now || Date.now, maxWait=options.maxPreviewWaitMs ?? 5000;
  let active=0,previews=0;
  const queue:Array<{preview:boolean;queuedAt:number;start:()=>void}>=[];
  const pump=()=>{
    while(active<limit){
      let index=queue.findIndex(e=>e.preview && previews<Math.max(1,limit-1) && now()-e.queuedAt>=maxWait);
      if(index<0)index=queue.findIndex(e=>!e.preview);
      if(index<0)index=queue.findIndex(e=>e.preview&&previews<Math.max(1,limit-1));
      if(index<0)return;
      queue.splice(index,1)[0]!.start();
    }
  };
  return function run<T>(task:()=>Promise<T>,preview=false):Promise<T>{
    const result=new Promise<T>((resolve,reject)=>{
      queue.push({preview,queuedAt:now(),start:AsyncResource.bind(()=>{
        active++;if(preview)previews++;
        void Promise.resolve().then(task).then(resolve,reject).finally(()=>{active--;if(preview)previews--;pump();});
      })});pump();
    });
    void result.catch(()=>{});return result;
  };
}
