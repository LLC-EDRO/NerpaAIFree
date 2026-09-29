import { llmJson } from './llm.js';
type Request = Parameters<typeof llmJson>[0];
type Entry = { input: Request; resolve: (v:any)=>void; reject: (e:unknown)=>void };
/** Share immutable sources/instructions across ready slides; each slide still
 * receives its own full semantic review and independently routed issues. */
export function createReviewBatch(call: typeof llmJson = llmJson, delayMs = 350) {
  const groups = new Map<string, Map<AbortSignal|undefined, Entry[]>>();
  return (input:Request):Promise<any> => new Promise((resolve,reject)=>{
    const payload = input.payload as any;
    const key = JSON.stringify([input.folder,input.prompt,payload.topic,payload.userSource]);
    let signals=groups.get(key);if(!signals)groups.set(key,signals=new Map());
    let pending=signals.get(input.signal);
    if(!pending){
      pending=[];signals.set(input.signal,pending);const batch=pending;
      setTimeout(async()=>{
        signals!.delete(input.signal);if(!signals!.size)groups.delete(key);
        try{
          input.signal?.throwIfAborted();
          if(batch.length===1){batch[0].resolve(await call(batch[0].input));return;}
          const facts=new Map<string,any>();
          const slides=batch.map(({input},i)=>{
            const {topic,userSource,researchEvidence,...rest}=input.payload as any;
            for(const fact of researchEvidence || [])facts.set(fact.id,fact);
            return {slide:i+1,...rest};
          });
          const values:any=await call({...input,stage:'content-review-batch',
            prompt:input.prompt+'\nПереданы независимые слайды. Проверь каждый по тем же правилам. Не требуй переносить текст между слайдами. Ответ {slides:[{slide:1,issues:[]}]}, все индексы ровно один раз; ключи полей относятся только к своему слайду.',
            payload:{topic:payload.topic,userSource:payload.userSource,researchEvidence:[...facts.values()],slides},
            validate:(raw:any)=>{
              if(!Array.isArray(raw?.slides)||raw.slides.length!==batch.length||new Set(raw.slides.map((s:any)=>s.slide)).size!==batch.length||raw.slides.some((s:any)=>!Number.isInteger(s.slide)||s.slide<1||s.slide>batch.length))throw new Error('Incomplete content review batch');
              return batch.map(({input},i)=>input.validate(raw.slides.find((s:any)=>s.slide===i+1)));
            },
          });
          batch.forEach((entry,i)=>entry.resolve(values[i]));
        }catch(e){batch.forEach(entry=>entry.reject(e));}
      },delayMs);
    }
    pending.push({input,resolve,reject});
  });
}
