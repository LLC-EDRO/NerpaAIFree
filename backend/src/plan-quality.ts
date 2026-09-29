import { evidenceNumbers, planContentNumbers, type Outline } from './domain.js';

/** Numeric matching is advisory: citations and paraphrases are not schema errors. */
export function planQualityWarnings(plan: Outline, facts: string, evidenceIds: string[], locale = 'ru') {
  const supported = new Set(evidenceNumbers(facts));
  return plan.slides.flatMap((s, i) => {
    const missing = [...new Set(planContentNumbers(`${s.title}\n${s.brief}`, evidenceIds)
      .filter(n => !supported.has(n) && n !== String(i + 1)))];
    return missing.length ? [{ slide: i + 1, reason: 'plan_numbers_review', message: locale === 'en'
      ? `Please check these outline figures against the sources: ${missing.join(', ')}.`
      : `Проверьте по источникам значения в плане: ${missing.join(', ')}.` }] : [];
  });
}
