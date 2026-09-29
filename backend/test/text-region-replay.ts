/** Regression replay against the saved VK WorkSpace failure, no model calls. */
import "dotenv/config";
import { resolve, join } from "node:path";
import assert from "node:assert/strict";
import { native } from "../src/pipeline.js";
import { readJson, writeJson } from "../src/store.js";
const folder = resolve(process.argv[2]);
const p = await readJson(join(folder, "project.json"));
const before = await readJson(
  join(folder, "diagnostics/text-region-before/filled-r3.json"),
);
const fit = await native("fit", join(folder, `assembled-r${p.revision}`), {
  slides: before.slides,
});
await writeJson(
  join(folder, "diagnostics/text-region-before/fit-new.json"),
  fit,
);
assert.ok(
  fit.issues.some(
    (i: any) =>
      i.slide === 12 &&
      i.key === "s460" &&
      i.details.includes("source_text_reserved_region"),
  ),
);
console.log(JSON.stringify(fit, null, 2));
