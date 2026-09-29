import { setTimeout as delay } from "node:timers/promises";
import { HttpError } from "./errors.js";

/** Delivery recovery is separate from content correction. Never retry a bad
 * key, exhausted quota, refusal or user cancellation as a transient outage. */
export async function providerJson(input: {
  url: string;
  init: RequestInit;
  signal?: AbortSignal;
  timeoutMs: number;
  prefix: "llm" | "search";
  model: string;
  fetcher?: typeof fetch;
  decode?: (response: Response) => Promise<any>;
  delayMs?: number;
  onRetry?: (event: {
    attempt: number;
    code: string;
    status?: number;
    waitMs: number;
  }) => Promise<void>;
}): Promise<any> {
  for (let attempt = 0; ; attempt++) {
    input.signal?.throwIfAborted();
    let status: number | undefined,
      waitMs = (input.delayMs ?? 1000) * 2 ** attempt;
    let failure: HttpError;
    try {
      const response = await (input.fetcher || fetch)(input.url, {
        ...input.init,
        signal: AbortSignal.any([
          AbortSignal.timeout(input.timeoutMs),
          ...(input.signal ? [input.signal] : []),
        ]),
      });
      status = response.status;
      if (!response.ok) {
        const body = (await response.json().catch(() => undefined)) as any;
        const quota = [body?.error?.code, body?.error?.type].some((v) =>
          ["insufficient_quota", "billing_hard_limit_reached"].includes(v),
        );
        failure = new HttpError(
          502,
          `Провайдер вернул HTTP ${status}.`,
          `${input.prefix}_http`,
          { status, model: input.model },
        );
        if (quota || ![408, 409, 429, 500, 502, 503, 504].includes(status))
          throw failure;
        const retryAfter = response.headers.get("retry-after");
        if (retryAfter) {
          const seconds = Number(retryAfter);
          const requested = Number.isFinite(seconds)
            ? seconds * 1000
            : Date.parse(retryAfter) - Date.now();
          if (Number.isFinite(requested))
            waitMs = Math.min(30000, Math.max(waitMs, requested));
        }
      } else {
        const result = await (input.decode ? input.decode(response) : response.json());
        input.signal?.throwIfAborted();
        return result;
      }
    } catch (error) {
      input.signal?.throwIfAborted();
      if (error instanceof HttpError) throw error;
      failure = new HttpError(
        503,
        "Провайдер не завершил передачу ответа после автоматических повторов.",
        `${input.prefix}_transport`,
      );
    }
    if (attempt >= 2) throw failure!;
    await input.onRetry?.({
      attempt: attempt + 1,
      code: failure!.code,
      status,
      waitMs,
    });
    await delay(waitMs, undefined, { signal: input.signal });
  }
}
