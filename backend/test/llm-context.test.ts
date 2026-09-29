import test from "node:test";
import assert from "node:assert/strict";
import {
  selectFacts,
  compactCopy,
  compactHistory,
  fillContext,
  needsRepairImage,
  compactIssues,
  compactTemplate,
} from "../src/llm-context.js";
import { mergeRepairCandidate } from "../src/repair.js";

const pool = Array.from({ length: 30 }, (_, i) => ({
  id: `f${i}`,
  text:
    i < 12
      ? `Энергетика: показатель ${i + 40}% среди обследованных предприятий в 2025 году.`
      : `Образование: число участников ${i + 100} в регионе.`,
  sourceIds: [`src${i}`],
}));
test("model receives usable text region while native slide geometry stays intact", () => {
  const region = { x: 20, y: 30, w: 180, h: 70, blockerRole: "icon" };
  const slot = {
    key: "arbitrary",
    x: 20,
    y: 30,
    w: 180,
    h: 180,
    maxChars: 45,
    textRegion: region,
  };
  const result = compactTemplate({ slots: [slot], charts: [] } as any, {
    fields: [{ key: slot.key, action: "replace" }],
    charts: [],
  });
  assert.deepEqual(result.fields[0].textRegion, region);
  assert.equal(result.fields[0].h, 180);
  assert.equal(slot.h, 180);
});
test("retrieval preserves exact cited facts and scope, expands to all on repeated failure", () => {
  const previous = {
    fields: { a: { text: "129", evidence: [pool[29].text] } },
    charts: {},
  };
  const selected = selectFacts(pool, "Энергетика предприятий 43%", previous);
  assert.ok(selected.length < pool.length);
  assert.ok(selected.includes(pool[29]));
  assert.ok(selected.includes(pool[3]));
  assert.ok(selected.every((f) => pool.includes(f)));
  assert.deepEqual(selectFacts(pool, "Энергетика", previous, 2), pool);
  assert.deepEqual(selectFacts(pool, "Незнакомая совершенно тема"), pool);
});
test("compact citations retain unknown user quotes and do not modify saved evidence", () => {
  const data = {
    fields: {
      a: { text: "40%", evidence: [pool[0].text, "Цитата пользователя"] },
    },
    charts: {},
  };
  assert.deepEqual(compactCopy(data, pool).fields.a.evidence, [
    "f0",
    "Цитата пользователя",
  ]);
  assert.equal(data.fields.a.evidence[0], pool[0].text);
});
test("repair prompt keeps related fields and whole-slide meaning without duplicated source structures", () => {
  const previous = {
    fields: {
      a: { text: "40%", evidence: [pool[0].text] },
      b: { text: "Предприятия", evidence: [pool[0].text] },
      c: { text: "Не менять заголовок", evidence: [] },
    },
    charts: {},
  };
  const layout: any = {
    id: "arbitrary",
    slots: ["a", "b", "c"].map((key) => ({
      key,
      x: 0,
      y: 0,
      w: 120,
      h: 40,
      size: 20,
      font: "Arial",
      text: "OLD SAMPLE",
      sourceFrameKey: key,
    })),
    charts: [],
  };
  const semantics = {
    fields: ["a", "b", "c"].map((key) => ({
      key,
      action: "replace",
      role: key === "a" ? "metric" : "body",
      group: key === "c" ? "title" : "metric",
      intent: "Энергетика",
    })),
    charts: [],
  };
  const issues = [
    { key: "a", reason: "overflow" },
    { key: "b", reason: "metric_context_changed" },
  ];
  const context = fillContext({
    topic: "Энергетика",
    userMaterials: "Нельзя менять период исследования.",
    plan: {
      slides: [
        {
          title: "Энергетика",
          brief: "Показатели предприятий",
          sourceLayoutId: "arbitrary",
        },
      ],
    },
    index: 0,
    layout,
    semantics,
    dimensions: { width: 1000, height: 600 },
    pool,
    frames: {},
    previous,
    issues,
    rejected: [],
    expansion: 0,
    failedRenderAvailable: true,
  });
  assert.deepEqual(
    context.template.fields.map((f: any) => f.key),
    ["a", "b"],
  );
  assert.deepEqual(context.repairContext?.allowedFieldKeys, ["a", "b"]);
  assert.equal(context.unchangedContent?.fields.c, previous.fields.c.text);
  assert.equal(context.userSource, "Нельзя менять период исследования.");
  assert.ok(!JSON.stringify(context).includes("OLD SAMPLE"));
  assert.deepEqual(context.previous?.fields.a.evidence, ["f0"]);
  const result = mergeRepairCandidate(
    {
      fields: {
        a: { text: "41%", evidence: ["f1"] },
        c: { text: "UNAUTHORIZED", evidence: [] },
      },
    },
    previous,
    issues,
  );
  assert.equal(result.fields.c.text, previous.fields.c.text);
});
test("repair history preserves recent rejected values, reason diversity and counts without growing forever", () => {
  const rejected = Array.from({ length: 20 }, (_, i) => ({
    key: "a",
    reason: i % 2 ? "overflow" : "content_mismatch",
    value: `v${i}`,
    contextHash: "large",
  }));
  const result = compactHistory(rejected, new Set(["a"]));
  assert.equal(result[0].attempts, 20);
  assert.equal(result[0].recent.length, 2);
  assert.deepEqual(
    result[0].recent.map((r) => r.value),
    ["v18", "v19"],
  );
  assert.equal(result[0].reasons.length, 2);
  assert.equal(rejected.length, 20);
});
test("visual conflicts keep images; a citation or factual correction does not need another image", () => {
  assert.equal(
    needsRepairImage([
      { reason: "overflow", details: ["source_text_frame_overflow"] },
    ]),
    true,
  );
  assert.equal(needsRepairImage([{ reason: "rendered_text_occluded" }]), true);
  assert.equal(needsRepairImage([{ reason: "evidence_not_in_source" }]), false);
  assert.equal(needsRepairImage([{ reason: "content_mismatch" }]), false);
});

test("evidence errors reference the exact available facts instead of duplicating their text", () => {
  const issues = [
    {
      key: "a",
      reason: "unsupported_number",
      unsupportedNumbers: ["40"],
      suggestedEvidence: [{ number: "40", candidates: [pool[0], pool[20]] }],
    },
  ];
  const compact = compactIssues(issues, [pool[0]]);
  assert.deepEqual(compact[0].suggestedEvidence, [
    { number: "40", candidateIds: ["f0"] },
  ]);
  assert.equal(compact[0].reason, "unsupported_number");
  assert.equal(issues[0].suggestedEvidence[0].candidates.length, 2);
  assert.ok(!JSON.stringify(compact).includes(pool[0].text));
});
test('description compaction retains model geometry and field identity without changing native precision or source text', async()=>{
 const {descriptionContext}=await import('../src/llm-context.js');
 const layout:any={id:'arbitrary',charts:[],slots:[{key:'unknown_key',shapeId:77,text:'Source prose '.repeat(100),role:'body',x:14.123456,y:20.654321,w:200.987654,h:100.234567,size:18,maxChars:100,cell:[1,2],textFit:{targetChars:80,lines:4,usableHeightPt:90}}]};
 const before=structuredClone(layout);const context=descriptionContext(layout);
 assert.deepEqual(layout,before);assert.equal(context.slots[0].key,'unknown_key');assert.equal(context.slots[0].x,14.1);assert.deepEqual(context.slots[0].cell,[1,2]);assert.equal(context.slots[0].text.length,240);assert.ok(layout.slots[0].text.length>240);
});

test('an unusably narrow paragraph frame asks for geometry assistance rather than a two-character summary',()=>{
 const layout:any={slots:[{key:'long',text:'Полноценное содержательное пояснение. '.repeat(8),maxChars:4,textFit:{targetChars:2},role:'body'}],charts:[]};
 const result=compactTemplate(layout,{fields:[{key:'long',role:'body',action:'replace'}]});
 assert.equal(result.fields[0].needsGeometryAssistance,true);
 assert.equal(result.fields[0].writingTargetChars,undefined);
});
