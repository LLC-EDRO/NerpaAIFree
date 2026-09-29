import "dotenv/config";
import { mkdir, copyFile, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
import { native } from "../src/pipeline.js";
import { writeJson } from "../src/store.js";
const root = resolve("data/native-acceptance"),
  source = join(root, "source"),
  assembled = join(root, "assembled"),
  output = join(root, "output");
for (const dir of [source, assembled, output])
  await mkdir(dir, { recursive: true });
await copyFile(
  resolve("../fixtures/cobalt-editorial.pptx"),
  join(source, "source.pptx"),
);
const sha = () =>
  readFile(join(source, "source.pptx")).then((b) =>
    createHash("sha256").update(b).digest("hex"),
  );
const before = await sha();
console.log("Analyze native source");
const original = await native("analyze", source);
const chart = original.layouts.find((l: any) => l.charts.length && l.usable);
assert.ok(chart, "fixture has an editable chart");
console.log("Assemble cover + repeated native chart");
await native("assemble", source, {
  layoutIds: [original.layouts[0].id, chart.id, chart.id],
  output: assembled,
});
await copyFile(
  join(assembled, "presentation.pptx"),
  join(assembled, "source.pptx"),
);
const analysis = await native("analyze", assembled);
assert.equal(analysis.layouts.length, 3);
const slides = analysis.layouts.map((l: any, i: number) => ({
  title: `Тест ${i + 1}`,
  native: {
    sourceSlideId: l.id,
    mode: "source",
    preserveTemplate: true,
    ordinal: i + 1,
    fields: Object.fromEntries(l.slots.map((s: any) => [s.key, s.text])),
    charts: Object.fromEntries(
      l.charts.map((c: any) => [
        c.key,
        {
          title: "Тестовые данные",
          categories: Array.from(
            { length: c.pointCount },
            (_, j) => `Этап ${j + 1}`,
          ),
          series: Array.from({ length: c.seriesCount }, (_, k) => ({
            name: `Ряд ${k + 1}`,
            values: Array.from(
              { length: c.pointCount },
              (_, j) => (i + 1) * 10 + j + k,
            ),
          })),
        },
      ]),
    ),
  },
}));
console.log("Fit and export independent chart values");
assert.deepEqual((await native("fit", assembled, { slides })).issues, []);
const result = await native("export", assembled, { slides, output });
await writeJson(join(root, "report.json"), result);
assert.deepEqual(result.issues, []);
assert.equal(await sha(), before);
await writeJson(join(root, "acceptance.json"), {
  passed: true,
  sourceSha256: before,
  slideCount: 3,
  repeatedChartSource: chart.id,
});
console.log(
  "PASS: native fit, duplicate layout, editable charts, PDF render, source unchanged",
);
