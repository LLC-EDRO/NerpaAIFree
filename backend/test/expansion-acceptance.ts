/** Real native fit -> PPTX -> PDF, no model requests or existing project edits. */
import "dotenv/config";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, join } from "node:path";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { native } from "../src/pipeline.js";
import { nativeSlide } from "../src/domain.js";
import { readJson, writeJson } from "../src/store.js";
const root = resolve("data/expansion-acceptance"),
  source = join(root, "source"),
  output = join(root, "output");
await mkdir(output, { recursive: true });
const hash = async () =>
  createHash("sha256")
    .update(await readFile(join(source, "source.pptx")))
    .digest("hex");
const before = await hash();
const analysis = await native("analyze", source);
assert.equal(analysis.layouts.length, 1);
const layout = analysis.layouts[0],
  title = layout.slots.find((s: any) => s.text === "Test");
assert.ok(title);
const fields = Object.fromEntries(
  layout.slots.map((s: any) => [
    s.key,
    {
      text: s.key === title.key ? "Современное образование" : s.text,
      evidence: [],
    },
  ]),
);
const slide = nativeSlide({ fields, charts: {} }, layout, "Test", 1);
const original = structuredClone(slide);
original.native.safeTextExpansion = false;
assert.ok(
  (await native("fit", source, { slides: [original] })).issues.some(
    (i: any) => i.key === title.key,
  ),
);
const fit = await native("fit", source, { slides: [slide] });
assert.deepEqual(fit.issues, []);
assert.equal(fit.fieldChanges.length, 1);
const change = fit.fieldChanges[0];
assert.equal(change.before.h, change.after.h);
assert.ok(change.after.w > change.before.w && change.after.w < 400);
const exported = await native("export", source, { slides: [slide], output });
assert.deepEqual(exported.issues, []);
assert.equal(await hash(), before);
const report = await readJson(join(output, "field-changes.json"));
assert.deepEqual(report.changes, fit.fieldChanges);
assert.equal(
  report.pptxSha256,
  createHash("sha256")
    .update(await readFile(join(output, "presentation.pptx")))
    .digest("hex"),
);
await writeJson(join(root, "result.json"), {
  passed: true,
  sourceUnchanged: true,
  fit,
  exported,
});
console.log(
  JSON.stringify({
    passed: true,
    changes: fit.fieldChanges,
    renderIssues: exported.issues,
  }),
);
