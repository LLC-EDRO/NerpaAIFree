/** Replay saved source-placeholder failure without provider calls. */
import "dotenv/config";
import assert from "node:assert/strict";
import { resolve, join } from "node:path";
import { native } from "../src/pipeline.js";
import { nativeSlide } from "../src/domain.js";
import { readJson, writeJson } from "../src/store.js";
const folder = resolve(process.argv[2]);
const p = await readJson(join(folder, "project.json"));
const old = await readJson(
  join(folder, "diagnostics/occlusion-before/copy-1.json"),
);
const spec = p.contract.slides[1];
const data = structuredClone(old.data);
data.fields.s267.text = "Аниме дополняет урок";
const slide = nativeSlide(data, spec.native, p.plan.slides[1].title, 2);
const fit = await native("fit", join(folder, `assembled-r${p.revision}`), {
  slides: [slide],
});
await writeJson(join(folder, "diagnostics/occlusion-fit.json"), fit);
assert.ok(
  fit.issues.every((i: any) =>
    i.details.includes("source_text_reserved_region"),
  ),
);
data.fields.s267.text = "Аниме дополняет\nурок";
const repaired = nativeSlide(data, spec.native, p.plan.slides[1].title, 2);
assert.deepEqual(
  (
    await native("fit", join(folder, `assembled-r${p.revision}`), {
      slides: [repaired],
    })
  ).issues,
  [],
);
const result = await native(
  "export",
  join(folder, `assembled-r${p.revision}`),
  { slides: [repaired], output: join(folder, "diagnostics/occlusion-preview") },
);
await writeJson(join(folder, "diagnostics/occlusion-render.json"), result);
assert.deepEqual(result.issues, []);
console.log(
  "PASS: previously rejected title fits the real partial-occlusion template",
);
