/** A missing literal citation is not a geometry failure. Derived figures and
 * uncited claims go to semantic review once; they never trigger recomposition
 * merely because the exact number is absent from the source. Keep the finding
 * as a warning even when the reviewer cannot establish a contradiction. */
export function numericReviewIssues(issues: any[]) {
  return {
    issues: issues.filter(i => i.reason !== 'unsupported_number'),
    warnings: issues.filter(i => i.reason === 'unsupported_number'),
  };
}
