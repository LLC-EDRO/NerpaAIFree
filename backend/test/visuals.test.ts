import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import sharp from "sharp";
import {
  generateVisualAsset,
  storeVisualAsset,
  editVisual,
  nearestVisualFields,
} from "../src/visuals.js";
import { presentationImageSize } from "../src/image-size.js";
import { receiptCost } from "../src/usage-cost.js";
import type { VisualChoice } from "../src/visual-types.js";
const choice: VisualChoice = {
  slideIndex: 0,
  slotIndex: 0,
  shapeId: 42,
  slot: {
    shapeId: 42,
    kind: "picture",
    box: { x: 350, y: 50, w: 300, h: 240 },
    aspectRatio: 1.25,
    sourceRole: "photo",
    protected: false,
    requiresOpaque: false,
    name: "test",
  },
  role: "content",
  kind: "illustration",
  description: "test picture",
  style: "blue",
  fieldKeys: [],
  background: "opaque",
  mode: "generate",
  quality: "medium",
  nonce: 0,
  instruction: "",
};
const dimensions = { width: 720, height: 405 };
test("image canvas is bounded and valid for wide, tall and square native slots", () => {
  for (const [w, h] of [
    [300, 200],
    [200, 300],
    [100, 100],
    [500, 30],
  ]) {
    const s = presentationImageSize({ x: 0, y: 0, w, h }, dimensions);
    assert.equal(s.width % 16, 0);
    assert.equal(s.height % 16, 0);
    assert.ok(s.width * s.height >= 655360 && s.width * s.height <= 1280 * 720);
    assert.ok(Math.max(s.width / s.height, s.height / s.width) <= 3);
  }
});
test("native image context follows spatial proximity, not XML order", () => {
  const layout: any = {
    slots: [
      { key: "far", x: 0, y: 0, w: 50, h: 20, role: "body" },
      { key: "near", x: 320, y: 60, w: 25, h: 100, role: "body" },
      { key: "brand", x: 350, y: 50, w: 10, h: 10, role: "brand" },
    ],
  };
  assert.deepEqual(
    nearestVisualFields(choice.slot, layout, {
      far: "far",
      near: "near",
      brand: "brand",
    }),
    ["near", "far"],
  );
});
test("image billing uses standard image rates and reports missing modality counts", () => {
  const c = receiptCost({
    model: "gpt-image-2",
    stage: "image-generation",
    usage: {
      input_tokens: 300,
      input_tokens_details: { text_tokens: 200, image_tokens: 100 },
      output_tokens: 1000,
    },
  });
  assert.equal(c.inputUsd, 0.0018);
  assert.equal(c.outputUsd, 0.03);
  assert.equal(c.totalUsd, 0.0318);
  assert.equal(
    receiptCost({
      model: "gpt-image-2",
      usage: { input_tokens: 300, output_tokens: 1000 },
    }).missingInput,
    1,
  );
});
test("normalize uploads; reject transparent text backdrops and fake image bytes", async () => {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-img-"));
  try {
    const png = await sharp({
      create: {
        width: 32,
        height: 32,
        channels: 4,
        background: { r: 1, g: 2, b: 3, alpha: 0.5 },
      },
    })
      .png()
      .toBuffer();
    assert.match(
      await storeVisualAsset(folder, png, true),
      /^[a-f0-9]{64}\.png$/,
    );
    await assert.rejects(
      storeVisualAsset(folder, png, false, true),
      /непрозрачное/,
    );
    await assert.rejects(
      storeVisualAsset(folder, Buffer.from("not an image")),
      /PNG/,
    );
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});
test("paid generation is cached; ambiguous calls never auto-repeat; explicit nonce permits retry", async () => {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-paid-"));
  const previous = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test";
  try {
    let calls = 0;
    const fetcher: any = async (_url: any, init: any) => {
      calls++;
      const body = JSON.parse(init.body);
      assert.equal(body.model, "gpt-image-2");
      assert.equal(body.n, 1);
      assert.equal(body.output_format, "png");
      assert.equal(body.response_format, undefined);
      const [width, height] = body.size.split("x").map(Number);
      const img = await sharp({
        create: { width, height, channels: 3, background: "#487af8" },
      })
        .png()
        .toBuffer();
      return new Response(
        JSON.stringify({
          data: [{ b64_json: img.toString("base64") }],
          usage: {
            input_tokens: 20,
            input_tokens_details: { text_tokens: 20, image_tokens: 0 },
            output_tokens: 500,
          },
        }),
        { headers: { "content-type": "application/json" } },
      );
    };
    const base = {
      folder,
      prompt: "test image",
      choice,
      dimensions,
      signal: new AbortController().signal,
      fetcher,
    };
    const [a, b] = await Promise.all([
      generateVisualAsset(base),
      generateVisualAsset(base),
    ]);
    assert.equal(a.asset, b.asset);
    assert.equal(calls, 1);
    const receipts = await readdir(join(folder, "llm"));
    assert.equal(receipts.length, 1);
    assert.ok(
      !(await readFile(join(folder, "llm", receipts[0]), "utf8")).includes(
        "b64_json",
      ),
    );
    let lost = 0;
    const broken = {
      ...base,
      prompt: "ambiguous",
      fetcher: (async () => {
        lost++;
        throw new Error("socket disconnected");
      }) as any,
    };
    await assert.rejects(generateVisualAsset(broken));
    await assert.rejects(generateVisualAsset(broken), /Автоповтор/);
    assert.equal(lost, 1);
    await generateVisualAsset({
      ...broken,
      choice: { ...choice, nonce: 1 },
      fetcher,
    });
    assert.equal(calls, 2);
  } finally {
    if (previous === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = previous;
    await rm(folder, { recursive: true, force: true });
  }
});
test("settings preserve unchanged output and reject stale revisions or protected logos", () => {
  const p: any = {
    revision: 1,
    contract: { assembledSha256: "identity", dimensions },
    result: { slides: 1 },
    visuals: {
      revision: 1,
      assembledSha256: "identity",
      choices: [structuredClone(choice)],
    },
  };
  const edit = { ...choice, revision: 1 };
  editVisual(p, edit);
  assert.deepEqual(p.result, { slides: 1 });
  assert.throws(
    () => editVisual(p, { ...edit, revision: 2 }),
    /Макет изменился/,
  );
  p.visuals.choices[0].slot.protected = true;
  assert.throws(() => editVisual(p, edit), /защищён/);
  p.visuals.choices[0].slot.protected = false;
  editVisual(p, { ...edit, instruction: "Change subject" });
  assert.equal(p.result, undefined);
  assert.equal(p.visuals.choices[0].applied, false);
});
