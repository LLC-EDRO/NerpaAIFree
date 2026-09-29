import { setTimeout as delay } from "node:timers/promises";

/** Retry transient delivery failures only. Validation, credentials and missing
 * configuration must not be retried as if another response could fix them. */
export async function retryTransient<T>(
  operation: () => Promise<T>,
  options: {
    retryable: (error: unknown) => boolean;
    signal?: AbortSignal;
    onRetry?: () => Promise<void>;
    attempts?: number;
    delayMs?: number;
  },
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    options.signal?.throwIfAborted();
    try {
      return await operation();
    } catch (error) {
      options.signal?.throwIfAborted();
      if (attempt + 1 >= (options.attempts ?? 2) || !options.retryable(error))
        throw error;
      await options.onRetry?.();
      await delay((options.delayMs ?? 1000) * (attempt + 1), undefined, {
        signal: options.signal,
      });
    }
  }
}
