import {AsyncResource} from 'node:async_hooks';
/** FIFO bounded work. Rejections are observed immediately; callers still receive them. */
export function createPool(limit: number, signal: AbortSignal) {
  if (!Number.isInteger(limit) || limit < 1)
    throw new Error("invalid_concurrency");
  let active = 0;
  const queue: Array<() => void> = [];
  function pump() {
    while (active < limit && queue.length) queue.shift()!();
  }
  return function run<T>(task: () => Promise<T>): Promise<T> {
    const result = new Promise<T>((resolve, reject) => {
      queue.push(AsyncResource.bind(() => {
        active++;
        void Promise.resolve()
          .then(() => {
            signal.throwIfAborted();
            return task();
          })
          .then(resolve, reject)
          .finally(() => {
            active--;
            pump();
          });
      }));
      pump();
    });
    void result.catch(() => {});
    return result;
  };
}
