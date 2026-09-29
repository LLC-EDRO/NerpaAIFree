import {engineEnv} from './runtime-context.js';
import { createPool } from './async-pool.js';

/** Drain in-flight work before exposing an error: no late project writes after
 * the job becomes idle. Queue cancellation does not discard finished copies. */
export async function parallelSlides<T>(items: T[], signal: AbortSignal,
  work: (item: T, index: number, signal: AbortSignal) => Promise<void>,
  limit = Math.max(1, Math.min(15, Math.floor(Number(engineEnv.SLIDE_CONCURRENCY) || 8)))) {
  const controller = new AbortController();
  const combined = AbortSignal.any([signal, controller.signal]);
  const pool = createPool(limit, combined);
  let failure: unknown;
  const results = await Promise.allSettled(items.map((item, i) => pool(async () => {
    try { await work(item, i, combined); }
    catch (error) { if (!controller.signal.aborted) { failure = error; controller.abort(error); } throw error; }
  })));
  signal.throwIfAborted();
  if (failure) throw failure;
  const rejected = results.find(r => r.status === 'rejected');
  if (rejected?.status === 'rejected') throw rejected.reason;
}
