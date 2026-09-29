import { digest } from './repair.js';
import { contentReviewVersion } from './content-review.js';
import { createReviewBatch } from './review-batch.js';
import { llmJson } from './llm.js';

const reviewBatch = createReviewBatch();
type Request = Parameters<typeof llmJson>[0];
/** Only the exact semantic request can reuse a receipt. Positions/font sizes
 * are deliberately absent from that request; groups and table bindings remain. */
export async function reviewRebuild(state: any, request: Request, call = reviewBatch) {
  request.signal?.throwIfAborted();
  const key = digest({ version: contentReviewVersion, prompt: request.prompt, payload: request.payload });
  if (state.contentReviews?.[key]) return request.validate(state.contentReviews[key]);
  try {
    const result = await call(request);
    state.contentReviews = { ...state.contentReviews, [key]: result };
    return result;
  } catch {
    request.signal?.throwIfAborted();
    // A reviewer outage must not discard a fully generated, fitting candidate.
    // No receipt is cached, and the UI explicitly reports the missing review.
    return {issues:[],unavailable:true};
  }
}

/** Identical proposals cannot improve a deterministic fit/render result.
 * Do not treat a changed candidate with the same issue label as a duplicate. */
export function identicalRebuildAttempt(attempts: any[], scene: unknown, data: unknown) {
  const key = digest({ scene, data });
  return attempts.find(a => a.issues?.length && digest({ scene: a.scene, data: a.data }) === key);
}
