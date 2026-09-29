import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, rm, readdir } from "node:fs/promises";
import { join } from "node:path";
import {
  changedPlanSlides,
  adaptationPatch,
  mergeAdaptation,
  adaptSelectedPlan,
  ensurePlanBaseline,
} from "../src/plan-adaptation.js";
import { projectDir, writeJson } from "../src/store.js";
const slide = (id: string, sourceLayoutId = "old") => ({
  id,
  sourceLayoutId,
  title: "Смысл",
  brief: "Подтверждённые факты",
});

test("layout and order changes adapt by identity; untouched slides remain untouched", () => {
  const before = { slides: [slide("a"), slide("b"), slide("c")] };
  const after = { slides: [slide("b"), slide("a", "new"), slide("c")] };
  assert.deepEqual(changedPlanSlides(before, after), [0, 1]);
  assert.deepEqual(changedPlanSlides(before, structuredClone(before)), []);
  const patches = adaptationPatch(
    {
      slides: [
        {
          index: 1,
          sourceLayoutId: "new",
          title: "Новый заголовок",
          brief: "Та же тема в новых блоках",
        },
      ],
    },
    after,
    [1],
    new Set(),
  );
  const merged = mergeAdaptation(after, patches.slides);
  assert.equal(merged.slides[1].id, "a");
  assert.equal(merged.slides[1].sourceLayoutId, "new");
  assert.deepEqual(merged.slides[0], after.slides[0]);
  assert.deepEqual(merged.slides[2], after.slides[2]);
});
test("model cannot change selection, address unrelated slides, duplicate or fabricate numbers", () => {
  const plan = { slides: [slide("a", "chosen"), slide("b")] };
  const valid = {
    index: 0,
    sourceLayoutId: "chosen",
    title: "Заголовок",
    brief: "Новые блоки",
  };
  for (const raw of [
    { slides: [{ ...valid, sourceLayoutId: "other" }] },
    { slides: [{ ...valid, index: 1 }] },
    { slides: [valid, valid] },
    { slides: [] },
    { slides: [{ ...valid, brief: "99% школ" }] },
  ])
    assert.throws(() => adaptationPatch(raw, plan, [0], new Set()));
});
async function fixture(t: any) {
  const id = randomUUID();
  const folder = projectDir(id);
  await mkdir(folder, { recursive: true });
  t.after(() => rm(folder, { recursive: true, force: true }));
  const baseline = {
    slides: Array.from({ length: 6 }, (_, i) => slide(String(i))),
  };
  const input = structuredClone(baseline);
  for (let i = 0; i < 5; i++) input.slides[i].sourceLayoutId = "chosen";
  const p: any = {
    id,
    revision: 3,
    events: [],
    plan: input,
    planAdaptation: { version: 1, baseline },
    brief: { topic: "Education", sourceText: "", count: 6, webSearch: false },
    analysis: {
      sha256: "source",
      layouts: [
        {
          id: "chosen",
          usable: true,
          index: 7,
          name: "Card",
          slots: [{ key: "title", maxChars: 40 }],
          charts: [],
        },
      ],
    },
  };
  return { p, folder, input };
}
test("batch repair resumes from durable cache, commits one revision and needs no repeated confirmation", async (t) => {
  const { p, folder, input } = await fixture(t);
  let calls = 0;
  const model: any = async (args: any) => {
    calls++;
    if (calls === 2) throw new Error("network interrupted");
    return args.validate({
      slides: args.payload.selected.map((s: any) => ({
        index: s.index,
        sourceLayoutId: s.sourceLayoutId,
        title: "Адаптированный заголовок",
        brief: "Прежний смысл в подходящих блоках",
      })),
    });
  };
  await assert.rejects(
    adaptSelectedPlan(p, new AbortController().signal, async () => ({}), model),
    /interrupted/,
  );
  assert.equal(p.revision, 3);
  assert.deepEqual(p.plan, input);
  assert.equal((await readdir(join(folder, "plan-adaptation"))).length, 1);
  assert.equal(
    await adaptSelectedPlan(
      p,
      new AbortController().signal,
      async () => ({}),
      model,
    ),
    true,
  );
  assert.equal(calls, 3);
  assert.equal(p.revision, 4);
  assert.deepEqual(p.plan.slides[5], input.slides[5]);
  assert.deepEqual(
    p.plan.slides.map((s: any) => s.sourceLayoutId),
    input.slides.map((s: any) => s.sourceLayoutId),
  );
  assert.equal(
    await adaptSelectedPlan(
      p,
      new AbortController().signal,
      async () => {
        throw new Error("must reuse completed adaptation");
      },
      model,
    ),
    false,
  );
  assert.equal(calls, 3);
});
test("legacy saved edit is compared to preceding revision", async (t) => {
  const { p, folder } = await fixture(t);
  await writeJson(join(folder, "plan-r2.json"), p.planAdaptation.baseline);
  delete p.planAdaptation;
  await ensurePlanBaseline(p);
  assert.deepEqual(
    changedPlanSlides(p.planAdaptation.baseline, p.plan),
    [0, 1, 2, 3, 4],
  );
});

test('actual field IDs in plan prose are not treated as factual numeric claims',()=>{
 const plan={slides:[slide('a','chosen')]};
 const raw={slides:[{index:0,sourceLayoutId:'chosen',title:'Процесс',brief:'В s418 заголовок, в chart27 диаграмма'}]};
 assert.doesNotThrow(()=>adaptationPatch(raw,plan,[0],new Set(),new Set(['s418','chart27'])));
 assert.throws(()=>adaptationPatch({...raw,slides:[{...raw.slides[0],brief:'В s418 результат 418 человек'}]},plan,[0],new Set(),new Set(['s418'])));
});

test('independent adaptation batches overlap but commit results in original slide order', {timeout:2000}, async (t) => {
  const {p,input}=await fixture(t);
  let entered=0, release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve});
  const model:any=async (args:any)=>{
    entered++;
    if(entered===2)release();
    await gate;
    assert.ok(args.payload.selected.every((s:any)=>!('id' in s)));
    return args.validate({slides:args.payload.selected.map((s:any)=>({index:s.index,sourceLayoutId:s.sourceLayoutId,title:'Адаптация',brief:'Прежний смысл'}))});
  };
  await adaptSelectedPlan(p,new AbortController().signal,async()=>({}),model);
  assert.equal(entered,2);
  assert.deepEqual(p.plan.slides.map((s:any)=>s.id),input.slides.map((s:any)=>s.id));
});

test('omitted adaptation rows receive a targeted AI retry and retain approved content after repeated omission', async (t) => {
  const {p,input}=await fixture(t);let targeted=0;
  const model:any=async (args:any)=>{
    if(args.stage.startsWith('plan-adapt-missing')){targeted++;return args.validate({slides:[]});}
    const selected=args.payload.selected.filter((s:any)=>s.index!==2);
    return args.validate({slides:selected.map((s:any)=>({index:s.index,sourceLayoutId:s.sourceLayoutId,title:'Адаптация',brief:'Прежний смысл'}))});
  };
  await adaptSelectedPlan(p,new AbortController().signal,async()=>({}),model);
  assert.equal(targeted,1);
  assert.deepEqual(p.plan.slides[2],input.slides[2]);
  assert.equal(p.plan.slides.length,input.slides.length);
  assert.equal(p.planAdaptation.warnings[0].slide,3);
});
test('one rejected row does not discard valid adaptations in a batch',()=>{
 const plan={slides:[slide('a','a'),slide('b','b')]};
 const result=adaptationPatch({slides:[{index:0,...plan.slides[0],brief:'99% организаций'},{index:1,...plan.slides[1],brief:'Сохранённый смысл'}]},plan,[0,1],new Set(),new Set(),true);
 assert.deepEqual(result.slides.map(s=>s.index),[1]);assert.equal(result.rejected[0].index,0);
});

test('explicit table columns survive prose adaptation without fraction false positives or extra requests', async(t)=>{
  const {p}=await fixture(t);
  p.brief.sourceText='Материал / Партий / Годность, % / Режим. Керамика: 120 / 94 / Спекание; Композит: 150 / 91 / Прессование.';
  let calls=0;
  const model:any=async(args:any)=>{calls++;return args.validate({slides:args.payload.selected.map((s:any)=>({index:s.index,sourceLayoutId:s.sourceLayoutId,title:'Материалы',brief:'Керамика — 120 партий, годность 94%; композит — 150 партий, годность 91%.'}))});};
  await adaptSelectedPlan(p,new AbortController().signal,async()=>({}),model);
  assert.equal(calls,2,'only the two independent batches; no repair calls');
  assert.equal(p.planAdaptation.warnings.length,0);
});
