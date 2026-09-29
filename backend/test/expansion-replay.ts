/** Check expansion policy against an existing deck without model calls/mutations. */
import "dotenv/config";
import { resolve, join } from "node:path";
import assert from "node:assert/strict";
import { native } from "../src/pipeline.js";
import { readJson, writeJson } from "../src/store.js";
const root = resolve(process.argv[2]),
  p = await readJson(join(root, "project.json"));
const filled = await readJson(join(root, `filled-r${p.revision}.json`));
const slides = filled.slides.map((s: any) => ({
  ...s,
  native: { ...s.native, safeTextExpansion: true },
}));
const folder = join(root, `assembled-r${p.revision}`),
  output = join(root, "diagnostics/expansion-replay");
assert.ok(
  !slides.some((s: any) => s.images?.length),
  "This replay preserves source pictures",
);
const fit = await native("fit", folder, { slides });
assert.deepEqual(fit.issues, []);
const exported = await native("export", folder, { slides, output });
assert.deepEqual(exported.issues, []);
await writeJson(join(output, "acceptance.json"), {
  passed: true,
  slides: slides.length,
  changes: exported.fieldChanges,
  issues: exported.issues,
});
console.log(
  JSON.stringify({
    passed: true,
    slides: slides.length,
    changes: exported.fieldChanges,
    issues: exported.issues,
  }),
);
