import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  renderWithRepair,
  digest,
  repeatedGeometryIssues,
  currentFitHistory,
  nativeFitVersion,
  renderedRepairImage,
  repairAnalysis,
} from "../src/repair.js";
import { readJson, writeJson } from "../src/store.js";
const issue = {
  slide: 1,
  key: "caption",
  reason: "overflow",
  details: ["rendered_text_outside_page"],
  suggestedMaxChars: 12,
};
test("obsolete partial-placeholder rejection is remeasured; proven full occlusion and rendered failures remain", () => {
  const old = {
    key: "title",
    reason: "unverifiable",
    details: ["source_picture_placeholder_occludes_text"],
  };
  const proven = { ...old, geometryVersion: 2, imageShapeId: 812 };
  const rendered = { ...old, details: ["rendered_text_occluded"] };
  assert.deepEqual(currentFitHistory([old, proven, rendered]), [
    proven,
    rendered,
  ]);
});
test("fitter upgrade releases old estimates but keeps renderer errors and current fit failures", () => {
  const old = {
    key: "caption",
    reason: "overflow",
    details: ["source_text_frame_overflow"],
    value: "Текст",
  };
  const current = {
    ...old,
    fitVersion: nativeFitVersion,
    overflowAxes: ["height"],
    message: "Объедини абзацы",
  };
  const render = { ...issue, key: "caption", value: "Текст" };
  assert.deepEqual(currentFitHistory([old, current, render]), [
    current,
    render,
  ]);
  const data = { fields: { caption: { text: "Текст" } }, charts: {} };
  assert.deepEqual(repeatedGeometryIssues(data, [old]), []);
  assert.equal(
    repeatedGeometryIssues(data, [current])[0].message,
    "Объедини абзацы",
  );
  assert.equal(repeatedGeometryIssues(data, [render]).length, 1);
});
const copy = (text: string) => ({
  fingerprint: "same-contract",
  passed: true,
  issues: [],
  rejected: [],
  data: {
    fields: {
      caption: { text, evidence: [] },
      good: { text: "82%", evidence: ["82% участников"] },
    },
    charts: {},
  },
});
async function fixture(t: any) {
  const output = await mkdtemp(join(tmpdir(), "nerpa-repair-"));
  t.after(() => rm(output, { recursive: true, force: true }));
  await writeJson(join(output, "copy-0.json"), copy("untouched"));
  await writeJson(join(output, "copy-1.json"), copy("too long caption"));
  return output;
}
test("AI repair receives only the image matching its failed content and render receipt", async (t) => {
  const output = await fixture(t);
  const image = join(output, "slide-1.png");
  let exports = 0;
  await renderWithRepair({
    output,
    identity: "vision",
    slideCount: 2,
    signal: new AbortController().signal,
    prepare: async () => {
      const current = await readJson(join(output, "copy-1.json"));
      if (exports) {
        assert.equal(await renderedRepairImage(output, 1, current.data), image);
        assert.equal(
          await renderedRepairImage(output, 0, current.data),
          undefined,
        );
        assert.equal(
          await renderedRepairImage(output, 1, copy("other draft").data),
          undefined,
        );
        await writeFile(image, "different render");
        assert.equal(
          await renderedRepairImage(output, 1, current.data),
          undefined,
        );
        current.data.fields.caption.text = "short";
      }
      return current.data;
    },
    render: async () => {
      exports++;
      await writeFile(image, "render bytes");
      return {
        issues: exports === 1 ? [issue] : [],
        renderQuality: { version: 6 },
      };
    },
    notify: async () => {},
  });
  assert.equal(exports, 2);
});
test("failure before rendering cannot attach a leftover preview", async (t) => {
  const output = await fixture(t);
  await writeFile(join(output, "slide-1.png"), "old image");
  await assert.rejects(
    renderWithRepair({
      output,
      identity: "fit-only",
      slideCount: 2,
      signal: new AbortController().signal,
      prepare: async () => "same",
      render: async () => ({ issues: [issue] }),
      notify: async () => {},
    }),
  );
  assert.equal(
    await renderedRepairImage(output, 1, copy("too long caption").data),
    undefined,
  );
});
test("model repair diagnosis is bounded and cannot authorize unknown field changes", () => {
  assert.equal(repairAnalysis({}, ["caption"]), undefined);
  assert.deepEqual(
    repairAnalysis(
      {
        repairAnalysis: {
          cause: "Не хватает высоты",
          action: "Объединить абзацы",
          fields: ["caption", "other", "caption", 1],
        },
      },
      ["caption"],
    ),
    {
      cause: "Не хватает высоты",
      action: "Объединить абзацы",
      fields: ["caption"],
    },
  );
});
test("render-only failure invalidates only affected slide and automatically renders again", async (t) => {
  const output = await fixture(t),
    original = await readJson(join(output, "copy-0.json"));
  let exports = 0,
    repairs = 0;
  await renderWithRepair({
    output,
    identity: "deck-v1",
    slideCount: 2,
    signal: new AbortController().signal,
    prepare: async () => {
      const bad = await readJson(join(output, "copy-1.json"));
      if (!bad.passed) {
        repairs++;
        assert.deepEqual(bad.issues, [issue]);
        assert.equal(bad.rejected[0].details[0], "rendered_text_outside_page");
        bad.data.fields.caption.text = "short";
        bad.passed = true;
        bad.issues = [];
        await writeJson(join(output, "copy-1.json"), bad);
      }
      return bad.data;
    },
    render: async () => ({ issues: ++exports === 1 ? [issue] : [] }),
    notify: async () => {},
  });
  assert.equal(exports, 2);
  assert.equal(repairs, 1);
  assert.deepEqual(await readJson(join(output, "copy-0.json")), original);
  const fixed = await readJson(join(output, "copy-1.json"));
  assert.deepEqual(fixed.data.fields.good, copy("").data.fields.good);
  assert.equal(
    (await readJson(join(output, "repair-state.json"))).complete,
    true,
  );
});
test("restart replays durable render error before accepting passed checkpoint", async (t) => {
  const output = await fixture(t),
    bad = await readJson(join(output, "copy-1.json"));
  await writeJson(join(output, "repair-state.json"), {
    identity: "deck-v1",
    rounds: 1,
    history: [],
    pending: [{ slide: 1, hash: digest(bad.data), issues: [issue] }],
  });
  await renderWithRepair({
    output,
    identity: "deck-v1",
    slideCount: 2,
    signal: new AbortController().signal,
    prepare: async () => {
      const current = await readJson(join(output, "copy-1.json"));
      assert.equal(current.passed, false);
      assert.deepEqual(current.issues, [issue]);
      return "repaired";
    },
    render: async () => ({ issues: [] }),
    notify: async () => {},
  });
});
test("identical failed payload cannot be exported forever", async (t) => {
  const output = await fixture(t);
  let exports = 0;
  await assert.rejects(
    renderWithRepair({
      output,
      identity: "same",
      slideCount: 2,
      signal: new AbortController().signal,
      prepare: async () => "unchanged",
      render: async () => {
        exports++;
        return { issues: [issue] };
      },
      notify: async () => {},
    }),
    (e: any) => e.code === "automatic_repair_no_progress",
  );
  assert.equal(exports, 1);
  assert.equal((await readJson(join(output, "copy-1.json"))).passed, false);
});
test("new candidates still stop at bounded render repair budget", async (t) => {
  const output = await fixture(t);
  let prepares = 0;
  await assert.rejects(
    renderWithRepair({
      output,
      identity: "same",
      slideCount: 2,
      maxRepairs: 2,
      signal: new AbortController().signal,
      prepare: async () => ++prepares,
      render: async () => ({ issues: [issue] }),
      notify: async () => {},
    }),
    (e: any) => e.code === "automatic_repair_exhausted",
  );
  assert.equal(prepares, 3);
});
test("old contract feedback cannot invalidate a new contract", async (t) => {
  const output = await fixture(t),
    bad = await readJson(join(output, "copy-1.json"));
  await writeJson(join(output, "repair-state.json"), {
    identity: "old",
    rounds: 4,
    history: [],
    pending: [{ slide: 1, hash: digest(bad.data), issues: [issue] }],
  });
  await renderWithRepair({
    output,
    identity: "new",
    slideCount: 2,
    signal: new AbortController().signal,
    prepare: async () => {
      assert.equal((await readJson(join(output, "copy-1.json"))).passed, true);
      return "new";
    },
    render: async () => ({ issues: [] }),
    notify: async () => {},
  });
});
test("bad renderer addressing fails closed, cancellation never publishes success", async (t) => {
  const output = await fixture(t);
  await assert.rejects(
    renderWithRepair({
      output,
      identity: "bad",
      slideCount: 2,
      signal: new AbortController().signal,
      prepare: async () => "x",
      render: async () => ({ issues: [{ ...issue, slide: 999 }] }),
      notify: async () => {},
    }),
    (e: any) => e.code === "automatic_repair_unaddressable",
  );
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    renderWithRepair({
      output,
      identity: "cancel",
      slideCount: 2,
      signal: controller.signal,
      prepare: async () => assert.fail("must not prepare"),
      render: async () => ({ issues: [] }),
      notify: async () => {},
    }),
    (e: any) => e.name === "AbortError",
  );
});
test("repeating clipped text is rejected, evidence-only corrections remain allowed", () => {
  const data = copy("caption").data;
  assert.equal(
    repeatedGeometryIssues(data, [
      { key: "caption", reason: "overflow", value: "caption" },
    ]).length,
    1,
  );
  assert.deepEqual(
    repeatedGeometryIssues(data, [
      { key: "caption", reason: "evidence_invalid", value: "caption" },
    ]),
    [],
  );
});

test("targeted partial replies merge before schema validation; other values cannot drift", async () => {
  const { mergeRepairCandidate } = await import("../src/repair.js");
  const previous = copy("long").data;
  const patch = {
    fields: {
      caption: { text: "short", evidence: [] },
      good: { text: "invented", evidence: [] },
    },
  };
  const merged = mergeRepairCandidate(patch, previous, [issue]);
  assert.equal(merged.fields.caption.text, "short");
  assert.deepEqual(merged.fields.good, previous.fields.good);
  assert.deepEqual(merged.charts, {});
  assert.equal(previous.fields.caption.text, "long");
  assert.throws(() =>
    mergeRepairCandidate({ fields: { unknown: "x" } }, previous, [issue]),
  );
});

test("export timeout retries automatically, validation errors and cancellation do not", async () => {
  const { retryTransient } = await import("../src/retry.js");
  let calls = 0;
  assert.equal(
    await retryTransient(
      async () => {
        if (++calls === 1) throw new Error("timeout");
        return "ok";
      },
      { retryable: (e) => (e as Error).message === "timeout", delayMs: 0 },
    ),
    "ok",
  );
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    retryTransient(
      async () => {
        calls++;
        throw new Error("validation");
      },
      { retryable: (e) => (e as Error).message === "timeout", delayMs: 0 },
    ),
  );
  assert.equal(calls, 1);
  const controller = new AbortController();
  await assert.rejects(
    retryTransient(
      async () => {
        controller.abort();
        throw new Error("timeout");
      },
      { signal: controller.signal, retryable: () => true, delayMs: 0 },
    ),
    (e: any) => e.name === "AbortError",
  );
});

test("metric correction includes its label but preserves other semantic groups", async () => {
  const { metricRepairIssues } = await import("../src/repair.js");
  const expanded = metricRepairIssues(
    [{ key: "number", reason: "overflow" }],
    [
      { key: "number", role: "metric", group: "kpi", action: "replace" },
      { key: "label", role: "metric_label", group: "kpi", action: "replace" },
      { key: "other", role: "body", group: "other", action: "replace" },
    ],
  );
  assert.deepEqual(
    expanded.map((e) => e.key),
    ["number", "label"],
  );
});

test("label fit failure can select a different supported metric in the same group", async () => {
  const { metricRepairIssues } = await import("../src/repair.js");
  const expanded = metricRepairIssues(
    [{ key: "label", reason: "overflow" }],
    [
      { key: "number", role: "metric", group: "kpi", action: "replace" },
      { key: "label", role: "metric_label", group: "kpi", action: "replace" },
      { key: "other", role: "metric", group: "other", action: "replace" },
    ],
  );
  assert.deepEqual(
    expanded.map((e) => e.key),
    ["label", "number"],
  );
});

test("repeated body failures unlock only replaceable fields in the same semantic group", async () => {
  const { semanticRepairIssues, mergeRepairCandidate } =
    await import("../src/repair.js");
  const fields = [
    { key: "a", role: "body", group: "message", action: "replace" },
    { key: "b", role: "body", group: "message", action: "replace" },
    { key: "brand", role: "brand", group: "message", action: "preserve" },
    { key: "other", role: "body", group: "other", action: "replace" },
  ];
  const problem = { key: "a", reason: "overflow" };
  assert.deepEqual(semanticRepairIssues([problem], fields, [problem]), [
    problem,
  ]);
  const expanded = semanticRepairIssues([problem], fields, [problem, problem]);
  assert.deepEqual(
    expanded.map((i) => i.key),
    ["a", "b"],
  );
  const previous = {
    fields: {
      a: { text: "long" },
      b: { text: "old" },
      brand: { text: "logo" },
      other: { text: "stable" },
    },
    charts: {},
  };
  const actual = mergeRepairCandidate(
    {
      fields: {
        a: { text: "short" },
        b: { text: "moved" },
        brand: { text: "bad" },
        other: { text: "bad" },
      },
    },
    previous,
    expanded,
  );
  assert.equal(actual.fields.b.text, "moved");
  assert.equal(actual.fields.brand.text, "logo");
  assert.equal(actual.fields.other.text, "stable");
  const citation = { key: "a", reason: "evidence_invalid" };
  assert.deepEqual(
    semanticRepairIssues([citation], fields, [citation, citation]),
    [citation],
  );
});

test('second failed render requests recomposition before the retry budget stops the deck',async t=>{
 const output=await fixture(t);let renders=0;let rebuilt=false;
 await renderWithRepair({output,identity:'last-resort',slideCount:2,signal:new AbortController().signal,
  prepare:async()=>{
   const path=join(output,'copy-1.json');const saved=await readJson(path);
   if(renders===1)assert.equal(saved.rebuildRequested,false);
   if(renders===2){assert.equal(saved.rebuildRequested,true);rebuilt=true;}
   saved.data.fields.caption.text='candidate '+renders;await writeJson(path,saved);return saved.data;
  },render:async()=>({issues:++renders<3?[issue]:[]}),notify:async()=>{},
 });
 assert.equal(rebuilt,true);assert.equal(renders,3);
});

test('warning policy publishes bounded unresolved render without another rebuild', async t => {
  const output = await fixture(t);
  let renders=0, prepares=0;
  const result = await renderWithRepair({output,identity:'warnings',slideCount:2,maxRepairs:1,signal:new AbortController().signal,
    prepare:async()=>++prepares,
    render:async()=>{renders++;return {issues:[issue],pptxWritten:true};},
    notify:async()=>{}, onUnresolved:async(result,issues)=>({...result,issues:[],warnings:issues}),
  });
  assert.equal(renders,2); assert.equal(prepares,2); assert.deepEqual(result.issues,[]);
  const state=await readJson(join(output,'repair-state.json'));
  assert.equal(state.complete,true);assert.deepEqual(state.pending,[]);assert.deepEqual(state.warnings,[issue]);
});
test('warning policy resumes an old terminal journal and keeps unaddressable diagnostics', async t => {
  const output=await fixture(t);
  await writeJson(join(output,'repair-state.json'),{identity:'terminal',rounds:7,terminal:'automatic_repair_exhausted',pending:[],history:[]});
  const issue={reason:'unknown_visual_problem'};
  let renders=0;
  await renderWithRepair({output,identity:'terminal',slideCount:2,signal:new AbortController().signal,
    prepare:async()=>({}),render:async()=>{renders++;return {issues:[issue]};},notify:async()=>{},
    onUnresolved:async(result,issues)=>({...result,issues:[],warnings:issues}),
  });
  assert.equal(renders,1);assert.deepEqual((await readJson(join(output,'repair-state.json'))).warnings,[issue]);
});
test('warning policy does not re-render identical content and never swallows cancellation', async t => {
  const output=await fixture(t); let renders=0;
  await renderWithRepair({output,identity:'repeat-warning',slideCount:2,signal:new AbortController().signal,
    prepare:async()=>({same:true}),render:async()=>{renders++;return {issues:[issue]};},notify:async()=>{},
    onUnresolved:async(result)=>({...result,issues:[]}),
  });
  assert.equal(renders,1);
  const controller=new AbortController();controller.abort();
  await assert.rejects(renderWithRepair({output,identity:'cancel-warning',slideCount:2,signal:controller.signal,
    prepare:async()=>assert.fail(),render:async()=>({issues:[]}),notify:async()=>{},onUnresolved:async r=>r,
  }),{name:'AbortError'});
});
