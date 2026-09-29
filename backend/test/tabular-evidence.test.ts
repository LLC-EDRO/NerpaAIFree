import test from "node:test";
import assert from "node:assert/strict";
import { tabularCellEvidence, tableColumnEvidence } from "../src/tabular-evidence.js";
import { validateCopy, numbers } from "../src/domain.js";
const source =
  "Таблица: Материал / Партий / Годность, % / Режим. Керамика: 120 / 94 / Спекание; Композит: 150 / 91 / Прессование.";
test('prose tables expose complete cells; standalone ratios and ambiguous rows do not',()=>{
  assert.deepEqual(tableColumnEvidence(source),['120','94','Спекание','150','91','Прессование']);
  assert.deepEqual(tableColumnEvidence('Отношение: 120 / 94. Доля: 1/3.'),[]);
  assert.deepEqual(tableColumnEvidence('Имя / Доля / Группа. Север: 1 / 3 / Основная.'),[]);
  assert.deepEqual(tableColumnEvidence('Имя / Значение / Группа. Север: 12.5 / Основная.'),['12.5','Основная']);
});
function fixture() {
  const rows = [
    ["Материал", "Партий", "Годность, %", "Режим"],
    ["Керамика", "120", "94", "Спекание"],
    ["Композит", "150", "91", "Прессование"],
  ];
  const slots = rows.flatMap((row, r) =>
    row.map((text, c) => ({
      key: `cell_${r}_${c}`,
      shapeId: 783,
      cell: [r, c],
      text,
      maxChars: 200,
      x: c * 200,
      y: r * 50,
      w: 180,
      h: 40,
      size: 14,
      role: "body",
    })),
  );
  const fields = Object.fromEntries(
    slots.map((s) => [s.key, { text: s.text, evidence: [source] }]),
  );
  return { slots, fields };
}
test("explicit source table columns support their own scalar values without paid rewriting", () => {
  const { slots, fields } = fixture();
  const semantics: any = {
    composition: "Table",
    fields: slots.map((s) => ({
      key: s.key,
      role: "body",
      group: "row",
      intent: "Cell",
      required: true,
      action: "replace",
    })),
    charts: [],
  };
  const checked = validateCopy(
    { fields, charts: {} },
    { id: "any", slots, charts: [] } as any,
    semantics,
    source,
  );
  assert.deepEqual(checked.issues, []);
  assert.equal(tabularCellEvidence(slots[5], slots, fields, [source]), "120");
  assert.equal(tabularCellEvidence(slots[6], slots, fields, [source]), "94");
});
test("ambiguous fractions, mismatched headers and row identity do not gain scalar exemptions", () => {
  const { slots, fields } = fixture();
  assert.deepEqual(numbers("120 / 94"), ["120/94"]);
  assert.equal(
    tabularCellEvidence(slots[5], slots, fields, [
      "Керамика: 120 / 94 / Спекание",
    ]),
    undefined,
  );
  fields.cell_0_1.text = "Год";
  assert.equal(
    tabularCellEvidence(slots[5], slots, fields, [source]),
    undefined,
  );
  fields.cell_0_1.text = "Партий";
  fields.cell_1_0.text = "Другой";
  assert.equal(
    tabularCellEvidence(slots[5], slots, fields, [source]),
    undefined,
  );
  fields.cell_1_0.text = "Керамика";
  assert.equal(
    tabularCellEvidence(slots[5], slots, fields, [
      source.replace("120 / 94", "1/2 / 94"),
    ]),
    undefined,
  );
  assert.equal(
    tabularCellEvidence({ ...slots[5], cell: undefined }, slots, fields, [
      source,
    ]),
    undefined,
  );
});
