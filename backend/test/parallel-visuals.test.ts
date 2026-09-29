import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile, rm, readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  createVisualSession,
  normalizeVisualPlan,
  generateVisualAsset,
  canAttachVisual,
} from "../src/visuals.js";
import {
  modelVisualSlots,
  validateVisualDescriptions,
  visualPreserveReason,
  type VisualChoice,
} from "../src/visual-types.js";
import { projectDir } from "../src/store.js";
const dimensions = { width: 960, height: 540 };
function choice(slideIndex = 0, slotIndex = 0): VisualChoice {
  return {
    slideIndex,
    slotIndex,
    shapeId: slotIndex + 10,
    slot: {
      shapeId: slotIndex + 10,
      kind: "picture",
      box: { x: 300, y: 50, w: 300, h: 300 },
      aspectRatio: 1,
      sourceRole: "photo",
      protected: false,
      requiresOpaque: false,
    },
    role: "content",
    kind: "illustration",
    description: `Subject ${slideIndex} ${slotIndex}`,
    style: "blue",
    fieldKeys: ["title"],
    background: "opaque",
    mode: "generate",
    quality: "medium",
    instruction: "",
    nonce: 0,
  };
}
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { resolve, promise };
};
test("icons remain native even if model or old saved settings demand generation", async () => {
  const icon = choice();
  icon.slot.sourceRole = "icon";
  icon.asset = "a".repeat(64) + ".png";
  icon.applied = true;
  const big = choice(1);
  big.slot.box = { x: 0, y: 0, w: 960, h: 540 };
  big.role = "photo_underlay";
  const p: any = {
    revision: 1,
    contract: { assembledSha256: "sha", dimensions },
    visuals: { revision: 1, assembledSha256: "sha", choices: [icon, big] },
  };
  assert.equal(normalizeVisualPlan(p), true);
  assert.equal(icon.mode, "keep");
  assert.equal(icon.applied, false);
  assert.ok(icon.asset);
  assert.equal(big.mode, "generate");
  assert.equal(normalizeVisualPlan(p), false);
  let calls = 0;
  await assert.rejects(
    generateVisualAsset({
      folder: "/unused",
      prompt: "image",
      choice: icon,
      dimensions,
      signal: new AbortController().signal,
      fetcher: (async () => {
        calls++;
      }) as any,
    }),
    /Иконка/,
  );
  assert.equal(calls, 0);
  const tiny = choice();
  tiny.slot.box = { x: 20, y: 20, w: 21, h: 21 };
  assert.match(visualPreserveReason(tiny, dimensions)!, /Маленький/);
});
test("exclude icons from AI description while retaining exact native slot indices", () => {
  const icon = choice().slot;
  icon.sourceRole = "icon";
  const picture = choice(0, 1).slot;
  const slots = [icon, picture];
  assert.deepEqual(
    modelVisualSlots(slots).map((s) => s.slotIndex),
    [1],
  );
  const raw = { ...choice(0, 1) };
  const described = validateVisualDescriptions([raw], slots, ["title"]);
  assert.equal(described[0].role, "icon");
  assert.equal(described[1].slotIndex, 1);
  assert.equal(
    validateVisualDescriptions(undefined, [icon], [])[0].role,
    "icon",
  );
  assert.throws(() => validateVisualDescriptions([], slots, []), /mapping/);
});
async function fixture(t: any) {
  const id = randomUUID(),
    folder = projectDir(id),
    asset = "b".repeat(64) + ".png";
  await mkdir(join(folder, "visual-assets"), { recursive: true });
  await writeFile(join(folder, "visual-assets", asset), "test image");
  t.after(() => rm(folder, { recursive: true, force: true }));
  const choices = [
    choice(0, 0),
    choice(0, 1),
    choice(1, 0),
    choice(1, 1),
    choice(2, 0),
    choice(2, 1),
  ];
  const p: any = {
    id,
    revision: 1,
    events: [],
    status: "filling",
    contract: {
      assembledSha256: "sha",
      dimensions,
      styleProfile: { colors: ["blue"] },
      slides: Array.from({ length: 3 }, () => ({ native: { slots: [] } })),
    },
    visuals: {
      revision: 1,
      assembledSha256: "sha",
      detectionVersion: 2,
      choices,
    },
  };
  const slides = Array.from({ length: 3 }, (_, i) => ({
    title: `Slide ${i}`,
    native: { fields: { title: `Final title ${i}` } },
  }));
  const brief: any = async (input: any) => {
    const raw = {
      briefs: Object.fromEntries(
        input.payload.slots.map((s: any) => [
          s.slotIndex,
          `Create a useful illustration for ${input.payload.title} with object ${s.slotIndex}`,
        ]),
      ),
    };
    return input.validate(raw);
  };
  return { p, folder, asset, slides, brief };
}
test("ready slides launch images before the deck is filled; three simultaneous calls; ordered assignments", async (t) => {
  const { p, folder, asset, slides, brief } = await fixture(t);
  const started = deferred(),
    three = deferred(),
    release = deferred();
  let active = 0,
    max = 0,
    calls = 0;
  const generate: any = async () => {
    active++;
    calls++;
    max = Math.max(max, active);
    started.resolve();
    if (calls === 3) three.resolve();
    await release.promise;
    active--;
    return { asset, identity: "request" };
  };
  const session = await createVisualSession(p, new AbortController().signal, {
    brief,
    generate,
  });
  session.enqueue(0, slides[0]);
  await started.promise;
  assert.ok(calls >= 1, "starts before later slides exist");
  session.enqueue(1, slides[1]);
  session.enqueue(2, slides[2]);
  await three.promise;
  assert.equal(active, 3);
  release.resolve();
  const images = await session.finish();
  await session.stop();
  assert.equal(calls, 6);
  assert.equal(max, 3);
  assert.equal(active, 0);
  assert.equal(Object.keys(images).length, 1);
  for (const slide of slides)
    assert.deepEqual(
      (slide as any).images.map((i: any) => i.slotIndex),
      [0, 1],
    );
  assert.equal(
    JSON.parse(
      await readFile(join(folder, "project.json"), "utf8"),
    ).visuals.choices.filter((c: any) => c.status === "ready").length,
    6,
  );
});
test("cancellation drains in-flight work and never sends queued paid requests", async (t) => {
  const { p, asset, slides, brief } = await fixture(t);
  const started = deferred();
  let calls = 0,
    active = 0;
  const generate: any = async ({ signal }: any) => {
    calls++;
    active++;
    started.resolve();
    await new Promise<void>((resolve) =>
      signal.addEventListener("abort", () => resolve(), { once: true }),
    );
    active--;
    signal.throwIfAborted();
    return { asset, identity: "unused" };
  };
  const session = await createVisualSession(p, new AbortController().signal, {
    brief,
    generate,
  });
  slides.forEach((s, i) => session.enqueue(i, s));
  await started.promise;
  await session.stop();
  assert.equal(active, 0);
  assert.ok(calls <= 3);
  await assert.rejects(session.finish());
});

test("explicit native image instruction is content even when AI mistakes the empty frame for decoration", () => {
  const c = choice();
  c.slot.kind = "placeholder";
  c.slot.sourceRole = "decoration";
  c.slot.detection = {
    method: "explicit_image_instruction",
    markerFieldKeys: ["marker"],
    markerShapeIds: [77],
    labels: ["Вставить фото"],
  };
  const result = validateVisualDescriptions(
    [
      {
        ...c,
        role: "decoration",
        background: "transparent",
        fieldKeys: ["title"],
      },
    ],
    [c.slot],
    ["title", "marker"],
  );
  assert.equal(result[0].role, "content");
  assert.equal(result[0].background, "transparent");
  assert.equal(
    visualPreserveReason({ ...c, ...result[0] }, dimensions),
    undefined,
  );
});

test("approved images start before text, run concurrently, attach once and reuse paid assets", async (t) => {
  const { p, asset, slides, brief } = await fixture(t);
  p.visuals.choices = [choice(0), choice(1)];
  p.plan = {
    slides: slides.map((s) => ({
      title: s.title,
      objective: "Approved new subject",
    })),
  };
  const both = deferred(),
    release = deferred();
  let calls = 0,
    briefs = 0;
  const deps: any = {
    brief: async (input: any) => {
      briefs++;
      assert.equal(input.payload.contentBasis, "approved_plan");
      assert.equal(input.payload.approved.objective, "Approved new subject");
      assert.deepEqual(input.payload.slots[0].nearbyText, []);
      return brief(input);
    },
    generate: async () => {
      if (++calls === 2) both.resolve();
      await release.promise;
      return { asset, identity: "paid" };
    },
  };
  const session = await createVisualSession(
    p,
    new AbortController().signal,
    deps,
  );
  session.prewarm();
  await both.promise;
  assert.equal(calls, 2);
  assert.equal((slides[0] as any).images, undefined);
  slides.slice(0, 2).forEach((s, i) => session.enqueue(i, s));
  release.resolve();
  await session.finish();
  await session.stop();
  assert.equal(calls, 2);
  assert.equal(briefs, 2);
  assert.equal((slides[0] as any).images.length, 1);
  const retry = await createVisualSession(
    p,
    new AbortController().signal,
    deps,
  );
  retry.prewarm();
  slides.slice(0, 2).forEach((s, i) => retry.enqueue(i, s));
  await retry.finish();
  await retry.stop();
  assert.equal(calls, 2);
  assert.equal(briefs, 2);
});

test("diagrams wait for final text; a briefing outage retains original without blocking text", async (t) => {
  const { p, slides } = await fixture(t);
  p.visuals.choices = [{ ...choice(), kind: "diagram" }];
  p.plan = { slides };
  let called = 0;
  const session = await createVisualSession(p, new AbortController().signal, {
    brief: async (input: any) => {
      called++;
      assert.equal(input.payload.contentBasis, undefined);
      throw new Error("provider unavailable");
    },
    generate: async () => {
      throw new Error("must not buy without a brief");
    },
  });
  session.prewarm();
  assert.equal(called, 0);
  session.enqueue(0, slides[0]);
  await session.finish();
  await session.stop();
  assert.equal(called, 1);
  assert.deepEqual((slides[0] as any).images, []);
  assert.equal(p.visuals.choices[0].status, "failed");
});

test("cancellation drains prewarmed requests even when no text slide was enqueued", async (t) => {
  const { p, slides, brief } = await fixture(t);
  p.plan = { slides };
  const started = deferred();
  let active = 0,
    calls = 0;
  const session = await createVisualSession(p, new AbortController().signal, {
    brief,
    generate: async ({ signal }: any) => {
      active++;
      calls++;
      started.resolve();
      await new Promise<void>((r) =>
        signal.addEventListener("abort", () => r(), { once: true }),
      );
      active--;
      signal.throwIfAborted();
      throw new Error("unreachable");
    },
  });
  session.prewarm();
  await started.promise;
  await session.stop();
  assert.equal(active, 0);
  assert.ok(calls <= 3);
  await assert.rejects(session.finish());
});

test("recomposed slides retain images only in preserved native frames", () => {
  const c = choice();
  const slide: any = {
    native: { rebuild: { pictures: [{ shapeId: c.shapeId, ...c.slot.box }] } },
  };
  assert.equal(canAttachVisual(slide, c), true);
  slide.native.rebuild.pictures[0].x += 50;
  assert.equal(canAttachVisual(slide, c), false);
  c.slot.kind = "placeholder";
  assert.equal(canAttachVisual(slide, c), true);
  c.slot.protected = true;
  assert.equal(canAttachVisual(slide, c), false);
  c.slot.protected = false;
  slide.native.preserveSource = true;
  assert.equal(canAttachVisual(slide, c), false);
});
