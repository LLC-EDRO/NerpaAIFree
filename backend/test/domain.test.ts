import test from "node:test";
import assert from "node:assert/strict";
import {
  evidenceNumbers,
  validateSemantics,
  validateCopy,
  type Layout,
  exactKeys,
  bindSemanticsToPlan,
  nativeSlide,
} from "../src/domain.js";
import {
  sandboxArtifactAllowed,
  receiveSandboxOutput,
  validateSandboxArtifacts,
} from "../src/sandbox-protocol.js";
import { mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const layout: Layout = {
  id: "slide_1",
  index: 0,
  name: "Метрика",
  usable: true,
  warnings: [],
  charts: [],
  slots: [
    {
      key: "s2",
      text: "99%",
      shapeId: 2,
      role: "body",
      maxChars: 4,
      size: 70,
      x: 10,
      y: 10,
      w: 100,
      h: 80,
    },
  ],
};
const spec = {
  composition: "Метрика",
  fields: [
    {
      key: "s2",
      role: "metric",
      group: "metric",
      intent: "Доля участников",
      action: "replace",
      required: true,
    },
  ],
  charts: [],
};
test("each native key is required exactly once", () => {
  assert.throws(() => exactKeys(["s2", "s2"], ["s2", "s3"], "text"));
  assert.throws(() => validateSemantics({ ...spec, fields: [] }, layout));
});
test("sample statistics cannot be kept as original facts", () => {
  const semantics = validateSemantics(
    { ...spec, fields: [{ ...spec.fields[0], action: "preserve" }] },
    layout,
  );
  assert.equal(semantics.fields[0].action, "replace");
});
test("numbers require exact quotations from user materials", () => {
  const semantics = validateSemantics(spec, layout);
  const good = validateCopy(
    {
      fields: { s2: { text: "82%", evidence: ["82% завершили обучение"] } },
      charts: {},
    },
    layout,
    semantics,
    "82% завершили обучение",
  );
  assert.deepEqual(good.issues, []);
  const bad = validateCopy(
    {
      fields: { s2: { text: "99%", evidence: ["99% завершили обучение"] } },
      charts: {},
    },
    layout,
    semantics,
    "82% завершили обучение",
  );
  assert.equal(bad.issues.length, 1);
  assert.equal(bad.issues[0].reason, "unsupported_number");
  assert.equal(bad.citationWarnings.length, 1);
  assert.deepEqual(bad.data.fields.s2.evidence, []);
});
test("retrieved fact IDs resolve to exact saved evidence without accepting invented facts", () => {
  const semantics = validateSemantics(spec, layout);
  const pool = [{ id: "evidence-1", text: "82% завершили обучение" }];
  const value = {
    fields: { s2: { text: "82%", evidence: ["evidence-1"] } },
    charts: {},
  };
  const good = validateCopy(value, layout, semantics, pool[0].text, pool);
  assert.deepEqual(good.issues, []);
  assert.deepEqual(good.data.fields.s2.evidence, [pool[0].text]);
  assert.ok(
    validateCopy(value, layout, semantics, pool[0].text, []).issues.length,
  );
  assert.ok(
    validateCopy(value, layout, semantics, "Другая тема", pool).issues.length,
  );
  value.fields.s2.text = "99%";
  assert.ok(
    validateCopy(value, layout, semantics, pool[0].text, pool).issues.some(
      (e) => e.reason === "unsupported_number",
    ),
  );
});
test("missing sample-size citation reports the exact number and matching fact ID", () => {
  const pool = [
    { id: "evidence-1", text: "В опросе участвовали 440 преподавателей." },
    { id: "evidence-2", text: "84,5% опрошенных применяли чат-боты." },
  ];
  const value = {
    fields: {
      s2: { text: "84,5% из 440 преподавателей", evidence: ["evidence-2"] },
    },
    charts: {},
  };
  const result = validateCopy(
    value,
    layout,
    validateSemantics(spec, layout),
    pool.map((e) => e.text).join("\n"),
    pool,
  );
  assert.deepEqual(result.issues[0].unsupportedNumbers, ["440"]);
  assert.equal(
    result.issues[0].suggestedEvidence[0].candidates[0].id,
    "evidence-1",
  );
  value.fields.s2.evidence.push("evidence-1");
  assert.deepEqual(
    validateCopy(
      value,
      layout,
      validateSemantics(spec, layout),
      pool.map((e) => e.text).join("\n"),
      pool,
    ).issues,
    [],
  );
});
test("native lists remove duplicate typed markers but keep ordinary text and negative values", () => {
  const semantics = validateSemantics(spec, layout);
  const value = {
    fields: { s2: { text: "• Первый\n• Второй\n− минус", evidence: [] } },
    charts: {},
  };
  assert.equal(
    validateCopy(value, layout, semantics, "", [], { s2: ["•"] }).data.fields.s2
      .text,
    "Первый\nВторой\n− минус",
  );
  assert.equal(
    validateCopy(value, layout, semantics, "").data.fields.s2.text,
    value.fields.s2.text,
  );
});
test("mandatory fields cannot be cleared to pass fit", () => {
  assert.throws(() =>
    validateSemantics(
      { ...spec, fields: [{ ...spec.fields[0], action: "clear" }] },
      layout,
    ),
  );
  const value = validateCopy(
    { fields: { s2: { text: "", evidence: [] } }, charts: {} },
    layout,
    validateSemantics(spec, layout),
    "",
  );
  assert.equal(value.issues[0].reason, "required_text");
});
test("speaker sample binds to approved subtitle without altering layout or raw contract", () => {
  const raw = {
    ...spec,
    fields: [
      { ...spec.fields[0], role: "body", intent: "Имя спикера и должность" },
    ],
  };
  const before = JSON.stringify({ raw, layout });
  const bound = bindSemanticsToPlan(raw, layout, {
    sourceLayoutId: layout.id,
    title: "ИИ в образовании",
    brief: "Тема и подзаголовок о применении ИИ в России",
  });
  assert.equal(bound.fields[0].group, "subtitle");
  assert.equal(bound.fields[0].required, true);
  assert.equal(JSON.stringify({ raw, layout }), before);
  assert.deepEqual(
    bindSemanticsToPlan(bound, layout, {
      sourceLayoutId: layout.id,
      title: "ИИ в образовании",
      brief: "Тема и подзаголовок о применении ИИ в России",
    }),
    bound,
  );
});
test("missing speaker may be blank but ordinary required content stays required", () => {
  const approved = {
    sourceLayoutId: layout.id,
    title: "Итоги",
    brief: "Финальный слайд",
  };
  const bound = bindSemanticsToPlan(
    {
      ...spec,
      fields: [
        { ...spec.fields[0], role: "body", intent: "Имя спикера и должность" },
      ],
    },
    layout,
    approved,
  );
  assert.equal(bound.fields[0].required, false);
  assert.deepEqual(
    validateCopy(
      { fields: { s2: { text: "", evidence: [] } }, charts: {} },
      layout,
      bound,
      "",
    ).issues,
    [],
  );
  assert.equal(
    bindSemanticsToPlan(spec, layout, approved).fields[0].required,
    true,
  );
});
test("closing caption can still contain an approved explanation instead of a speaker", () => {
  const approved = {
    sourceLayoutId: layout.id,
    title: "Спасибо",
    brief: "Краткий вывод об ответственном использовании ИИ",
  };
  const bound = bindSemanticsToPlan(
    {
      ...spec,
      fields: [
        {
          ...spec.fields[0],
          role: "body",
          intent: "Имя, должность или дополнительное пояснение",
        },
      ],
    },
    layout,
    approved,
  );
  assert.equal(bound.fields[0].required, false);
  assert.match(bound.fields[0].intent, /пояснение по утверждённому плану/);
  assert.deepEqual(bindSemanticsToPlan(bound, layout, approved), bound);
});
test("step ordinals are distinct from unsupported statistics", () => {
  const step = { ...layout, slots: [{ ...layout.slots[0], text: "01" }] };
  const semantics = validateSemantics(
    {
      ...spec,
      fields: [{ ...spec.fields[0], intent: "Порядковый номер шага" }],
    },
    step,
  );
  assert.equal(semantics.fields[0].role, "step_number");
  assert.deepEqual(
    validateCopy(
      { fields: { s2: { text: "02", evidence: [] } }, charts: {} },
      step,
      semantics,
      "",
    ).issues,
    [],
  );
  assert.ok(
    validateCopy(
      { fields: { s2: { text: "82%", evidence: [] } }, charts: {} },
      step,
      semantics,
      "",
    ).issues.length,
  );
});
test("chart values and category counts are grounded and checked", () => {
  const chartLayout = {
    ...layout,
    slots: [],
    charts: [
      { key: "chart1", seriesCount: 1, pointCount: 2, type: "barChart" },
    ],
  };
  const semantics = validateSemantics(
    {
      composition: "",
      fields: [],
      charts: [{ key: "chart1", intent: "Количество" }],
    },
    chartLayout,
  );
  const result = validateCopy(
    {
      fields: {},
      charts: {
        chart1: {
          title: "",
          categories: ["А", "Б"],
          series: [{ name: "Команды", values: [12, 90] }],
          evidence: ["В пилоте 12 команд"],
        },
      },
    },
    chartLayout,
    semantics,
    "В пилоте 12 команд",
  );
  assert.equal(result.issues[0].reason, "chart_evidence");
});
test("sandbox refuses traversal and incomplete PPTX output", () => {
  assert.equal(sandboxArtifactAllowed("assemble", "../.env"), false);
  assert.equal(sandboxArtifactAllowed("assemble", "presentation.pptx"), true);
  assert.throws(() => validateSandboxArtifacts("assemble", {}, [], 3));
});
test("source slide enables bounded expansion without accepting model geometry", () => {
  const slide = nativeSlide(
    { fields: { s2: { text: "Заголовок", evidence: [] } }, charts: {} },
    layout,
    "Тема",
    1,
  );
  assert.equal(slide.native.safeTextExpansion, true);
  assert.equal(slide.native.preserveTemplate, true);
  assert.equal(slide.native.fields.s2, "Заголовок");
});
test("expansion audit artifact is required when the exporter reports field changes", () => {
  const files = [
    "presentation.pptx",
    "presentation.pdf",
    "render-quality.json",
    "package-cleanup.json",
    "slide-0.png",
  ];
  assert.equal(sandboxArtifactAllowed("export", "field-changes.json"), true);
  assert.throws(() =>
    validateSandboxArtifacts(
      "export",
      { issues: [], fieldChanges: [] },
      files,
      1,
    ),
  );
  assert.doesNotThrow(() =>
    validateSandboxArtifacts(
      "export",
      { issues: [], fieldChanges: [] },
      [...files, "field-changes.json"],
      1,
    ),
  );
});
test("framed sandbox output handles chunk boundaries and rejects truncated streams", async () => {
  const dir = await mkdtemp(join(tmpdir(), "lab-protocol-test-"));
  try {
    await mkdir(join(dir, "good"));
    const payload = Buffer.from(
      JSON.stringify({ result: { slideCount: 1 } }) +
        "\n" +
        JSON.stringify({ file: "presentation.pptx", size: 3 }) +
        "\n" +
        JSON.stringify({ chunk: Buffer.from("abc").toString("base64") }) +
        "\n" +
        JSON.stringify({ endFile: true }) +
        "\n" +
        JSON.stringify({ done: true }) +
        "\n",
    );
    async function* chunks() {
      for (let i = 0; i < payload.length; i += 7)
        yield payload.subarray(i, i + 7);
    }
    const out = await receiveSandboxOutput(
      chunks(),
      "assemble",
      join(dir, "good"),
    );
    assert.deepEqual(out.files, ["presentation.pptx"]);
    assert.equal(
      await readFile(join(dir, "good/presentation.pptx"), "utf8"),
      "abc",
    );
    async function* broken() {
      yield Buffer.from('{"result":{}}\n');
    }
    await assert.rejects(() => receiveSandboxOutput(broken(), "assemble", dir));
  } finally {
    await rm(dir, { recursive: true });
  }
});

test("metric fields require sourced numeric facts, not empty or qualitative placeholders", () => {
  const semantics = validateSemantics(spec, layout);
  for (const text of [
    "",
    "—",
    "Н/Д",
    "Нет данных",
    "Развитие",
    "Нет данных за 2025",
  ])
    assert.ok(
      validateCopy(
        { fields: { s2: { text, evidence: [] } }, charts: {} },
        layout,
        semantics,
        "",
      ).issues.some((e) => e.reason === "metric_fact_required"),
    );
  assert.deepEqual(
    validateCopy(
      {
        fields: { s2: { text: "82%", evidence: ["82% завершили обучение"] } },
        charts: {},
      },
      layout,
      semantics,
      "82% завершили обучение",
    ).issues,
    [],
  );
});

test("spelled source counts support digits without extracting units from composite numerals", () => {
  const source =
    "Представители 15 вузов, одной научной организации и двух предприятий из двух стран.";
  assert.deepEqual(evidenceNumbers(source), ["15", "1", "2", "2"]);
  assert.ok(!evidenceNumbers("двадцать один участник").includes("1"));
  assert.ok(!evidenceNumbers("две тысячи двадцать пять").includes("5"));
  assert.ok(!evidenceNumbers("две тысячи").includes("2"));
  const result = validateCopy(
    {
      fields: {
        s2: { text: "1 организация, 2 предприятия", evidence: [source] },
      },
      charts: {},
    },
    layout,
    validateSemantics(spec, layout),
    source,
  );
  assert.deepEqual(result.issues, []);
  const bad = validateCopy(
    {
      fields: { s2: { text: "9 предприятий", evidence: [source] } },
      charts: {},
    },
    layout,
    validateSemantics(spec, layout),
    source,
  );
  assert.ok(bad.issues.some((e) => e.reason === "unsupported_number"));
});
test('merged native table cells are not forced to invent numeric sample scores',()=>{
 const cellLayout:any={id:'any',slots:[{key:'cell',shapeId:8,cell:[1,1],text:'0.5'}],charts:[]};
 const result=validateSemantics({composition:'table',fields:[{key:'cell',role:'metric',group:'row',intent:'Вероятность из примера',action:'replace',required:true}],charts:[]},cellLayout);
 assert.equal(result.fields[0].role,'body');
 const checked=validateCopy({fields:{cell:{text:'Ежемесячный аудит',evidence:[]}},charts:{}},cellLayout,result,'');
 assert.deepEqual(checked.issues,[]);
 const bad=validateCopy({fields:{cell:{text:'99%',evidence:[]}},charts:{}},cellLayout,result,'');
 assert.ok(bad.issues.some(i=>i.reason==='unsupported_number'));
});

test('user-supplied material IDs validate without web research and reject changed quantities', async () => {
  const { materialFacts } = await import('../src/llm-context.js');
  const source = 'Получено 120 файлов; отобрано 84; публикация разрешена для 72.';
  const pool = materialFacts(source);
  const check = (text: string) => validateCopy({fields:{s2:{text,evidence:[pool[0].id]}},charts:{}}, layout, validateSemantics(spec,layout), source,pool);
  assert.deepEqual(check('120 файлов').issues,[]);
  assert.equal(check('999 файлов').issues[0].reason,'unsupported_number');
  assert.equal(materialFacts(source,pool).length,1);
  assert.equal(materialFacts('Другие материалы\n'+source)[1].id,pool[0].id);
});
