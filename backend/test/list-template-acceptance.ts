import "dotenv/config";
import assert from "node:assert/strict";
import { mkdir, readFile, copyFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { createHash } from "node:crypto";
import { native } from "../src/pipeline.js";
import { writeJson } from "../src/store.js";

const projectId = process.argv[2];
assert.ok(projectId, "project ID required");
const source = process.argv[3]
    ? resolve(process.argv[3])
    : resolve("data/projects", projectId, "source"),
  root = process.argv[4]
    ? resolve(process.argv[4])
    : resolve("data/validation/list-template-acceptance");
const assembled = join(root, "assembled"),
  output = join(root, "output");
await mkdir(assembled, { recursive: true });
await mkdir(output, { recursive: true });
const read = async (path: string) => JSON.parse(await readFile(path, "utf8"));
const sourceAnalysis = await read(join(source, "analysis.json"));
const hash = async () =>
  createHash("sha256")
    .update(await readFile(join(source, "source.pptx")))
    .digest("hex");
const originalHash = await hash();
const requested = process.argv[5]?.split(",");
const selected = sourceAnalysis.layouts.filter(
  (l: any) => l.usable && (!requested || requested.includes(l.id)),
);
assert.ok(selected.length);
if (requested) assert.equal(selected.length, requested.length);
console.log("Assemble", selected.length, "readable layouts");
await native("assemble", source, {
  layoutIds: selected.map((l: any) => l.id),
  output: assembled,
});
await copyFile(
  join(assembled, "presentation.pptx"),
  join(assembled, "source.pptx"),
);
const analysis = await native("analyze", assembled),
  frames = await read(join(assembled, "frames.json"));
assert.equal(analysis.layouts.length, selected.length);
assert.ok(analysis.layouts.every((l: any) => l.usable));
const slides = analysis.layouts.map((l: any, i: number) => ({
  title: `Проверка ${i + 1}`,
  native: {
    sourceSlideId: l.id,
    sourceOrdinal: i + 1,
    ordinal: i + 1,
    mode: "source",
    preserveTemplate: true,
    fields: Object.fromEntries(
      l.slots.map((s: any) => {
        const sourceParagraphs = frames[l.id][s.key].paragraphs.filter(
          (p: any) => p.has_text,
        );
        // One diagnostic record per cell, not all the source's old multi-line content.
        const paragraphs = s.cell
          ? sourceParagraphs.slice(0, 1)
          : sourceParagraphs;
        return [
          s.key,
          paragraphs
            .map((p: any) =>
              p.bullet_text ? "Пункт" : s.maxChars >= 12 ? "Проверка" : "А",
            )
            .join("\n") || "А",
        ];
      }),
    ),
    charts: {},
  },
}));
const fit = await native("fit", assembled, { slides });
await writeJson(join(root, "fit.json"), fit);
assert.deepEqual(fit.issues, []);
console.log("Export and verify native text, symbols, lists and PDF");
const result = await native("export", assembled, { slides, output });
await writeJson(join(root, "result.json"), result);
assert.deepEqual(result.issues, []);
assert.equal(await hash(), originalHash);
await writeJson(join(root, "acceptance.json"), {
  passed: true,
  projectId,
  slideCount: slides.length,
  sourceSha256: originalHash,
  bulletFontSubstitutions: sourceAnalysis.bulletFontSubstitutions,
});
console.log(
  "PASS: all readable layouts filled and exported; original source unchanged.",
);
