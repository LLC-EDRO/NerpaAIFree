// Bounded live-provider replay of a saved real overflow, isolated from project output.
import "dotenv/config";
import assert from "node:assert/strict";
import { mkdir, readFile, readdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import {
  native,
  copyPrompt,
  repairPrompt,
  projectFacts,
} from "../src/pipeline.js";
import { llmJson } from "../src/llm.js";
import { fillContext, compactCopy, selectFacts } from "../src/llm-context.js";
import { validateCopy, nativeSlide } from "../src/domain.js";
import {
  mergeRepairCandidate,
  repairAnalysis,
  rejections,
} from "../src/repair.js";
import {
  contentReviewPrompt,
  parseContentReview,
} from "../src/content-review.js";
import { writeJson } from "../src/store.js";

const [projectId, ordinalText, key] = process.argv.slice(2);
assert.ok(
  projectId && ordinalText && key,
  "project id, slide ordinal, field key required",
);
const folder = resolve("data/projects", projectId),
  output = resolve("data/validation/context-repair-replay");
await mkdir(output, { recursive: true });
const read = async (path: string) => JSON.parse(await readFile(path, "utf8"));
const p = await read(join(folder, "project.json")),
  i = Number(ordinalText) - 1,
  spec = p.contract.slides[i];
const assembled = join(folder, `assembled-r${p.revision}`),
  frames = await read(join(assembled, "frames.json"));
const pool = p.research.evidence,
  source = projectFacts(p),
  signal = new AbortController().signal;
const receipts = await Promise.all(
  (await readdir(join(folder, "llm")))
    .filter((n) => n.endsWith(".json"))
    .sort()
    .map((n) => read(join(folder, "llm", n))),
);
const initial = receipts
  .filter((r) => r.stage === `fill-${i + 1}-0` && r.inputContext)
  .at(-1);
assert.ok(initial, "requires a saved compact-context generation receipt");
let data = validateCopy(
  JSON.parse(initial.response),
  spec.native,
  spec.semantics,
  source,
  pool,
).data;
const original = structuredClone(data),
  sourceOrdinal = spec.index + 1;
const fit = async () =>
  (
    await native("fit", assembled, {
      slides: [
        nativeSlide(
          data,
          spec.native,
          p.plan.slides[i].title,
          i + 1,
          sourceOrdinal,
        ),
      ],
    })
  ).issues || [];
let issues = await fit();
assert.ok(
  issues.some((x: any) => x.key === key && x.reason === "overflow"),
  "must reproduce a genuine saved overflow",
);
const rejected: any[] = [],
  checks: any[] = [];
let diagnosis: any;
for (let round = 0; round < 3 && issues.length; round++) {
  assert.ok(
    issues.every((x: any) => x.key === key),
    "replay is limited to the selected field",
  );
  const previous = data;
  rejected.push(...rejections(previous, issues, "replay"));
  const context = fillContext({
    topic: p.brief.topic,
    userMaterials: p.brief.sourceText,
    plan: p.plan,
    index: i,
    layout: spec.native,
    semantics: spec.semantics,
    dimensions: p.contract.dimensions,
    pool,
    frames: frames[spec.native.id],
    previous,
    issues,
    rejected,
    expansion: round,
    failedRenderAvailable: false,
    diagnosis,
  });
  const candidate = await llmJson({
    stage: `fill-repair-replay-${round}`,
    folder: output,
    signal,
    useVisionModel: true,
    prompt: copyPrompt + repairPrompt,
    images: [join(assembled, "previews", `slide-${i}.png`)],
    payload: { ...context, imageRoles: ["source_layout"] },
    validate: (raw) => ({
      ...validateCopy(
        mergeRepairCandidate(raw, previous, issues),
        spec.native,
        spec.semantics,
        source,
        pool,
      ),
      diagnosis: repairAnalysis(raw, [key]),
    }),
  });
  data = candidate.data;
  diagnosis = candidate.diagnosis;
  issues = candidate.issues.length ? candidate.issues : await fit();
  checks.push({ round, issues, diagnosis });
}
assert.deepEqual(issues, []);
for (const k of Object.keys(original.fields))
  if (k !== key) assert.deepEqual(data.fields[k], original.fields[k]);
const review = await llmJson({
  stage: "content-review-repair-replay",
  folder: output,
  signal,
  prompt: contentReviewPrompt,
  payload: {
    topic: p.brief.topic,
    userSource: p.brief.sourceText,
    researchEvidence: selectFacts(pool, p.plan.slides[i].brief, data),
    approved: p.plan.slides[i],
    semantics: spec.semantics,
    data: compactCopy(data, pool),
  },
  validate: (v) => parseContentReview(v, data),
});
assert.deepEqual(review.issues, []);
await writeJson(join(output, "report.json"), {
  projectId,
  slide: i + 1,
  key,
  before: original.fields[key],
  after: data.fields[key],
  checks,
  review,
  otherFieldsUnchanged: true,
});
console.log(
  "PASS: real saved overflow repaired by AI; native fit and content review pass; other fields unchanged.",
);
