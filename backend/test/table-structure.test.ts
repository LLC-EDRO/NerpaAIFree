import test from "node:test";
import assert from "node:assert/strict";
import {
  applyTablePlans,
  regularTables,
  validateTablePlans,
  tableRowRepairIssues,
} from "../src/table-structure.js";
import { nativeSlide, type Layout } from "../src/domain.js";

const base: Layout = {
  id: "arbitrary_slide",
  index: 0,
  name: "Grid",
  usable: true,
  warnings: [],
  charts: [],
  slots: [
    {
      key: "title",
      shapeId: 2,
      role: "header",
      text: "Title",
      maxChars: 40,
      size: 24,
      x: 0,
      y: 0,
      w: 600,
      h: 40,
    },
    ...[0, 1].flatMap((r) =>
      [0, 1, 2].map((c) => ({
        key: `s47_r${r}_c${c}`,
        shapeId: 47,
        cell: [r, c],
        role: r ? "table_cell" : "table_header",
        text: "Sample",
        maxChars: 300,
        size: 18,
        x: 30 + c * 200,
        y: r ? 100 : 60,
        w: 200,
        h: r ? 300 : 40,
      })),
    ),
  ],
};
const semantics = {
  composition: "table",
  charts: [],
  fields: base.slots.map((s) => ({
    key: s.key,
    role: "body",
    group: s.key,
    intent: "sample",
    required: true,
    action: "replace",
  })),
};
const plan = {
  tables: [
    {
      shapeId: 47,
      columns: ["Мероприятие", "Дата", "Результат"],
      rows: ["Событие А", "Событие Б", "Событие В"],
    },
  ],
};

test("capacity estimate never removes existing dense native rows", () => {
  const dense = structuredClone(base);
  dense.slots = Array.from({length: 8}, (_, r) => [0,1,2].map(c => ({
    ...base.slots[1],key:`s47_r${r}_c${c}`,cell:[r,c],x:30+c*200,y:60+r*25,h:25,
  }))).flat();
  assert.equal(regularTables(dense)[0].maxDataRows,7);
  assert.equal(validateTablePlans({tables:[{...plan.tables[0],rows:Array.from({length:7},(_,i)=>`Record ${i}`)}]},dense).tables[0].rows.length,7);
});

test("native table records create rows within unchanged bounds and column widths", () => {
  const validated = validateTablePlans(plan, base);
  const { layout, semantics: meaning } = applyTablePlans(
    base,
    semantics,
    validated,
  );
  assert.equal(layout.slots.filter((s) => s.cell).length, 12);
  assert.deepEqual(layout.tableRows, { "47": 3 });
  assert.deepEqual(layout.slots[0], base.slots[0]);
  for (const s of layout.slots.filter((s) => s.cell && s.cell[0] > 0)) {
    assert.equal(s.h, 100);
    assert.equal(s.w, 200);
    assert.equal(s.size, 18);
    assert.equal(s.y, 100 + (s.cell[0] - 1) * 100);
    assert.equal(
      meaning.fields.find((f: any) => f.key === s.key).group,
      `table_47_row_${s.cell[0]}`,
    );
  }
  assert.equal(base.slots.length, 7);
  const fields = Object.fromEntries(
    layout.slots.map((s) => [s.key, { text: "Текст", evidence: [] }]),
  );
  assert.deepEqual(
    nativeSlide({ fields, charts: {} }, layout, "Тема", 1).native.tableRows,
    { "47": 3 },
  );
});

test("column changes, invented table ids and duplicate records are rejected", () => {
  for (const table of [
    { ...plan.tables[0], shapeId: 48 },
    { ...plan.tables[0], columns: ["one"] },
    { ...plan.tables[0], rows: ["same", "same"] },
    {
      ...plan.tables[0],
      rows: Array.from({ length: 12 }, (_, i) => String(i)),
    },
  ])
    assert.throws(() => validateTablePlans({ tables: [table] }, base));
  assert.throws(() =>
    validateTablePlans({ tables: [plan.tables[0], plan.tables[0]] }, base),
  );
});

test("merged or irregular grids are kept out of automatic resizing", () => {
  const merged = structuredClone(base);
  merged.slots = merged.slots.filter((s) => s.key !== "s47_r0_c1");
  assert.deepEqual(regularTables(merged), []);
  const irregular = structuredClone(base);
  irregular.slots.find((s) => s.key === "s47_r1_c1")!.w = 180;
  assert.deepEqual(regularTables(irregular), []);
});

test("physical repair includes only cells of the affected record, never a neighbouring row", () => {
  const { layout } = applyTablePlans(base, semantics, plan);
  const issues = tableRowRepairIssues(
    [{ key: "s47_r2_c1", reason: "overflow" }],
    layout,
  );
  assert.deepEqual(
    new Set(issues.map((i) => i.key)),
    new Set(["s47_r2_c0", "s47_r2_c1", "s47_r2_c2"]),
  );
});
test("semantic table repair includes column heading and its values without unlocking unrelated cells", () => {
  const { layout } = applyTablePlans(base, semantics, plan);
  const keys = new Set(tableRowRepairIssues([{key:"s47_r2_c1",reason:"content_mismatch"}],layout).map(i=>i.key));
  assert.deepEqual(keys,new Set(["s47_r2_c1","s47_r0_c0","s47_r0_c1","s47_r0_c2","s47_r1_c1","s47_r3_c1","s47_r2_c0","s47_r2_c2"]));
  assert.ok(!keys.has("title"));assert.ok(!keys.has("s47_r3_c0"));
});
test('strict table schema is derived from native columns and measured row capacity',async()=>{
 const {tablePlanJsonSchema}=await import('../src/table-structure.js');
 const schema=tablePlanJsonSchema(base),table=schema.properties.tables.items.anyOf[0];
 assert.equal(schema.additionalProperties,false);assert.equal(table.properties.columns.minItems,2);assert.equal(table.properties.columns.maxItems,3);
 assert.equal(table.properties.rows.maxItems,regularTables(base)[0].maxDataRows);
 assert.deepEqual(table.properties.shapeId.enum,[47]);
});
test('row redistribution does not reuse capacity measured for a different native cell height',()=>{
 const withBudgets=structuredClone(base);
 for(const slot of withBudgets.slots)if(slot.cell)slot.textFit={targetChars:500,lines:20};
 const plans=validateTablePlans({tables:[{shapeId:47,columns:['A','B','C'],rows:['first record','second record']}]},withBudgets);
 const result=applyTablePlans(withBudgets,semantics,plans);
 assert.ok(result.layout.slots.filter(s=>s.cell?.[0]>0).every(s=>s.textFit===undefined));
 assert.ok(withBudgets.slots.filter(s=>s.cell).every(s=>s.textFit?.targetChars===500));
});
