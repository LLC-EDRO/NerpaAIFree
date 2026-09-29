/** Validate explicit image frames on a real deck; no image-provider calls. */
import "dotenv/config";
import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { mkdir } from "node:fs/promises";
import sharp from "sharp";
import { native } from "../src/pipeline.js";
import { readJson, writeJson } from "../src/store.js";
const folder = resolve(process.argv[2]),
  p = await readJson(join(folder, "project.json"));
const assembled = join(folder, `assembled-r${p.revision}`),
  output = join(folder, "diagnostics/image-markers-replay");
await mkdir(output, { recursive: true });
const metadata = await native("layout_metadata", assembled);
const detected = p.contract.slides.flatMap((s: any, i: number) =>
  (metadata[s.assembledLayoutId].visualSlots || [])
    .filter((v: any) => v.detection)
    .map((v: any) => ({ index: i, slot: v })),
);
assert.deepEqual(
  detected.map((d: any) => d.index + 1),
  [4, 7, 10, 13],
);
const filled = await readJson(join(folder, `filled-r${p.revision}.json`));
const asset = join(
  output,
  "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.png",
);
await sharp(
  Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500"><rect width="1000" height="500" fill="#087cff"/><circle cx="500" cy="250" r="120" fill="#7ae3ee"/></svg>',
  ),
)
  .png()
  .toFile(asset);
const slides = detected.map(({ index, slot }: any) => {
  const s = structuredClone(filled.slides[index]);
  for (const key of slot.detection.markerFieldKeys)
    assert.equal(s.native.fields[key], "");
  const slotIndex = metadata[s.native.sourceSlideId].visualSlots.findIndex(
    (v: any) => v.shapeId === slot.shapeId,
  );
  return {
    ...s,
    visualSlotsVersion: 1,
    images: [
      {
        shapeId: slot.shapeId,
        slotIndex,
        image:
          "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.png",
        background: "opaque",
      },
    ],
  };
});
assert.deepEqual((await native("fit", assembled, { slides })).issues, []);
const result = await native("export", assembled, {
  slides,
  output,
  images: {
    "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.png":
      asset,
  },
});
assert.deepEqual(result.issues, []);
await writeJson(join(output, "acceptance.json"), {
  passed: true,
  detected,
  issues: result.issues,
  paidImageCalls: 0,
});
console.log(
  JSON.stringify({
    passed: true,
    slides: detected.map((d: any) => d.index + 1),
    issues: result.issues,
  }),
);
