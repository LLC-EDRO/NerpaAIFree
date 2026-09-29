import { HttpError } from './errors.js';
type Run = (action: string, folder: string, extra: Record<string, any>, signal?: AbortSignal) => Promise<any>;
type Entry = { slide: any; resolve: (v: any) => void; reject: (e: unknown) => void };
/** Coalesce independent single-slide checks without changing native validation.
 * Geometry requests keep their own repair-key context and bypass this batch. */
export function createFitBatch(run: Run, delayMs = 100, schedule: (task:()=>Promise<void>)=>Promise<void> = task=>task()) {
  const queues = new Map<string, Map<AbortSignal | undefined, Entry[]>>();
  return (folder: string, slide: any, signal?: AbortSignal): Promise<any> => new Promise((resolve, reject) => {
    let bySignal = queues.get(folder);
    if (!bySignal) queues.set(folder, bySignal = new Map());
    let entries = bySignal.get(signal);
    if (!entries) {
      entries = [];
      bySignal.set(signal, entries);
      const batch = entries;
      setTimeout(() => {
        void schedule(async () => {
        bySignal!.delete(signal);
        if (!bySignal!.size) queues.delete(folder);
        try {
          signal?.throwIfAborted();
          const result = await run('fit', folder, { slides: batch.map(e => e.slide) }, signal);
          signal?.throwIfAborted();
          // Reject malformed routing rather than silently dropping an issue.
          if (result.error || !Array.isArray(result.issues) || (result.slideErrors!==undefined && !Array.isArray(result.slideErrors)) || [...result.issues,...(result.slideErrors || [])].some((v: any) => !Number.isInteger(v.slide) || v.slide < 0 || v.slide >= batch.length))
            throw new Error('invalid_native_batch_result');
          if(result.slideErrors?.some((v:any)=>typeof v.code!=='string'||!/^pptx_[a-z0-9_]+$/.test(v.code)) || new Set((result.slideErrors||[]).map((v:any)=>v.slide)).size!==(result.slideErrors||[]).length)
            throw new Error('invalid_native_batch_result');
          batch.forEach((e, i) => {
            const failure=result.slideErrors?.find((v:any)=>v.slide===i);
            if(failure){e.reject(new HttpError(422,'Не удалось проверить структуру этого слайда',failure.code));return;}
            e.resolve({
            ...result,
            issues: result.issues.filter((v: any) => v.slide === i).map((v: any) => ({ ...v, slide: 0 })),
            fieldChanges: (result.fieldChanges || []).filter((v: any) => v.slide === i + 1).map((v: any) => ({ ...v, slide: 1 })),
            geometryOptions: {},
          });});
        } catch (error) { batch.forEach(e => e.reject(error)); }
        }).catch(error => batch.forEach(e => e.reject(error)));
      }, delayMs);
    }
    entries.push({ slide, resolve, reject });
  });
}
