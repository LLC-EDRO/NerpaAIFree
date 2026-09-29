import { z } from 'zod';
import { join } from 'node:path';
import { createReviewBatch } from './review-batch.js';
import { contentReviewPrompt, contentReviewVersion, parseContentReview } from './content-review.js';
import { validateCopy } from './domain.js';
import { compactCopy } from './llm-context.js';
import { digest } from './repair.js';
import { readJson, writeJson } from './store.js';

const reviewBatch = createReviewBatch();
const changesSchema = z.array(z.object({key:z.string(),text:z.string().max(4000),evidence:z.array(z.string()).default([])})).max(80);
const signature = (issue:any) => digest([issue.key,issue.reason,issue.details,issue.unsupportedNumbers]);
export function noWorseFit(before:any[], after:any[]) {
  return after.every(issue => before.some(old => signature(old) === signature(issue) &&
    (!Number.isFinite(issue.measuredHeightPt) || !Number.isFinite(old.measuredHeightPt) || issue.measuredHeightPt <= old.measuredHeightPt) &&
    (!Number.isFinite(issue.measuredWidthPt) || !Number.isFinite(old.measuredWidthPt) || issue.measuredWidthPt <= old.measuredWidthPt)));
}

/** Cosmetic fit warnings must not skip meaning validation. One bounded,
 * batched text-only pass on the chosen candidate; never another layout loop. */
export async function reviewFinalCopy(input: {
  folder:string; output:string; index:number; topic:string; source:string; approved:any;
  slide:any; data:any; layout:any; semantics:any; pool:any[]; signal:AbortSignal;
  fit:(slide:any)=>Promise<any[]>;
}, call:typeof reviewBatch = reviewBatch) {
  input.signal.throwIfAborted();
  const identity=digest({version:1,review:contentReviewVersion,source:input.source,approved:input.approved,
    slide:input.slide,data:input.data,semantics:input.semantics,layout:input.layout});
  const path=join(input.output,`final-copy-review-${input.index}.json`);
  try {
    const saved=await readJson(path);
    if (saved.identity===identity) return saved.result;
  } catch(error) { if ((error as NodeJS.ErrnoException).code!=='ENOENT') throw error; }
  const unchanged={slide:input.slide,data:input.data,warnings:[] as any[],reviewed:true};
  try {
    const result=await call({folder:input.folder,stage:`content-review-final-${input.index+1}`,signal:input.signal,reasoning:'low',
      prompt:contentReviewPrompt + '\nЭто финальный выбранный вариант после исправления геометрии. Проверь СВЯЗИ соседних полей: заголовок карточки + её показатель/подпись, а не только наличие всех чисел где-либо на слайде. Группы и frame показывают, какие поля читатель видит вместе. Для конкретных противоречий сразу предложи минимальные текстовые changes:[{key,text,evidence:[точная цитата источника]}]. Меняй только поле, для которого вернул существенную issue с точным claim и sourceQuote. Не меняй дизайн, не дописывай пропущенные подробности, не улучшай стиль ради стиля. Сохрани нумерацию и короткую длину текста. Для правильного слайда верни {issues:[],changes:[]}.',
      payload:{topic:input.topic,userSource:input.source,approved:input.approved,data:compactCopy(input.data,input.pool),
        readingGroups:[...new Set(input.semantics.fields.map((f:any)=>f.group))].map(group=>({group,
          text:input.semantics.fields.filter((f:any)=>f.group===group).map((f:any)=>({key:f.key,text:input.data.fields[f.key]?.text}))})),
        fields:input.layout.slots.map((s:any)=>({key:s.key,frame:[s.x,s.y,s.w,s.h],cell:s.cell,
          ...input.semantics.fields.find((f:any)=>f.key===s.key)}))},
      validate:(raw:any)=>{
        const review=parseContentReview(raw,input.data,input.source,input.semantics.fields);
        const changes=changesSchema.parse(raw.changes || []);
        if (new Set(changes.map(c=>c.key)).size!==changes.length) throw new Error('duplicate_final_copy_patch');
        for (const c of changes) {
          if (!input.data.fields[c.key] || input.semantics.fields.find((f:any)=>f.key===c.key)?.action!=='replace' || !review.issues.some(i=>i.key===c.key))
            throw new Error('ungrounded_final_copy_patch');
        }
        return {...review,changes};
      },
    });
    const warnings=[...result.issues,...result.notes.map((note:any)=>({...note,reason:'content_review_note'}))];
    let output={...unchanged,warnings};
    if (result.changes.length) {
      const raw=structuredClone(input.data);
      for (const change of result.changes) raw.fields[change.key]={text:change.text,evidence:change.evidence};
      const baseline=validateCopy(input.data,input.layout,input.semantics,input.source,input.pool);
      const checked=validateCopy(raw,input.layout,input.semantics,input.source,input.pool);
      if (noWorseFit(baseline.issues,checked.issues)) {
        const candidate=structuredClone(input.slide);
        candidate.native.fields=Object.fromEntries(Object.entries<any>(checked.data.fields).map(([k,v])=>[k,v.text]));
        const [before,after]=await Promise.all([input.fit(input.slide),input.fit(candidate)]);
        if (noWorseFit(before,after)) {
          const changed=new Set(result.changes.map((c:{key:string})=>c.key));
          output={slide:candidate,data:checked.data,reviewed:true,warnings:[...warnings.filter(w=>!changed.has(w.key)),
            {reason:'content_connections_repaired',message:'Уточнены связи заголовков, подписей и показателей по исходным материалам.'}]};
        }
      }
    }
    await writeJson(path,{identity,result:output});
    return output;
  } catch(error) {
    input.signal.throwIfAborted();
    return {...unchanged,reviewed:false,warnings:[{reason:'content_review_unavailable',message:'Финальная проверка смысла не завершилась. Проверьте подписи и показатели; файл сохранён.'}]};
  }
}
