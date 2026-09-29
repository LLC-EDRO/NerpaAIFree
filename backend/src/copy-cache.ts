import { createHash } from "node:crypto";
export const acceptedWarningVersion = 1;
const advisoryReasons = new Set(['unsupported_number','chart_evidence','metric_fact_required','content_mismatch','invalid_evidence']);
/** Persist only exact advisory decisions. Changed data/review versions and all
 * physical fit failures must still be evaluated on the next pass. */
export function acceptCopyWarnings(data: unknown, issues: any[], reviewVersion: number) {
  return {version:acceptedWarningVersion, dataHash:hash(data), reviewVersion,
    signatures:issues.filter(i=>advisoryReasons.has(i.reason)).map(issueSignature)};
}
const issueSignature = (issue: any) => hash([issue.key,issue.reason,issue.unsupportedNumbers,issue.claim,issue.sourceQuote]);
export function unresolvedCopyIssues(issues: any[], data: unknown, receipt: any, reviewVersion: number) {
  if (receipt?.version !== acceptedWarningVersion || receipt?.dataHash !== hash(data) || receipt?.reviewVersion !== reviewVersion) return issues;
  const accepted = new Set(receipt.signatures);
  return issues.filter(i=>!advisoryReasons.has(i.reason) || !accepted.has(issueSignature(i)));
}
const hash = (value: unknown) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex");
function textLayout(layout: any) {
  if (!layout) return layout;
  const { visualSlots, imageSlots, images, geometryRepairs, ...text } = layout;
  return text;
}
function textSemantics(semantics: any) {
  if (!semantics) return semantics;
  const { images, ...text } = semantics;
  return text;
}
export function copyCacheFingerprint(context: {
  contextVersion: number;
  spec: any;
  source: string;
  plan: unknown;
  sha: string;
}) {
  const spec = { ...context.spec };
  for (const key of ["native", "sourceNative"])
    if (spec[key]) spec[key] = textLayout(spec[key]);
  for (const key of ["semantics", "sourceSemantics"])
    if (spec[key]) spec[key] = textSemantics(spec[key]);
  return hash({ version: 7, ...context, spec });
}
/** Safe one-time migration: require an exact v6 match, including approved text,
 * facts, geometry and plan. Only image descriptions may use the original saved
 * semantic snapshot. No fuzzy acceptance or skipped content/fit validation. */
export function legacyCopyFingerprints(
  context: {
    contextVersion: number;
    spec: any;
    source: string;
    plan: unknown;
    sha: string;
  },
  originalImages?: unknown,
) {
  const values = [hash({ version: 6, ...context })];
  if (Array.isArray(originalImages)) {
    const spec = {
      ...context.spec,
      semantics: { ...context.spec.semantics, images: originalImages },
    };
    values.push(hash({ version: 6, ...context, spec }));
  }
  return values;
}
