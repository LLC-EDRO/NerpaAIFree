import { z } from "zod";
import type { Layout, Slot } from "./domain.js";

/** Header counts toward the user-facing ten-row limit. */
export const maxTableRows = 10;
export const maxTableDataRows = maxTableRows - 1;
export const tableContentPolicy = `Таблица подстраивается под подтверждённые данные. Одна строка — одна самостоятельная запись, один столбец — одно осмысленное свойство. Всего не более 10 строк, включая заголовок. Исходные строки и столбцы — вместимость образца, не квота заполнения: лишние убери, недостающие строки добавь в пределах maxDataRows. Не дополняй таблицу накопительными «топ-N», суммами подмножеств, повторами или вымышленными записями ради заполнения; итоговые строки допустимы только когда они нужны по заданию. Одно значение с единицей показывай целиком в одной ячейке. Не раскладывай разряды числа по столбцам миллионов, тысяч и остатка. Можно сократить количество столбцов до реально подтверждённых свойств: например, объект и показатель. Не придумывай свойства ради исходного числа столбцов. Если обязательных записей больше лимита, выбери релевантные и явно укажи охват выборки в заголовке таблицы; не выдавай частичную выборку за полный список.`;

export function regularTables(layout: Layout) {
  const ids = [
    ...new Set(layout.slots.filter((s) => s.cell).map((s) => s.shapeId)),
  ];
  return ids.flatMap((shapeId) => {
    const slots = layout.slots.filter((s) => s.shapeId === shapeId && s.cell);
    const rowCount = Math.max(...slots.map((s) => s.cell[0])) + 1;
    const columnCount = Math.max(...slots.map((s) => s.cell[1])) + 1;
    const at = (r: number, c: number) =>
      slots.find((s) => s.cell[0] === r && s.cell[1] === c);
    if (
      rowCount < 2 ||
      columnCount < 2 ||
      columnCount > 8 ||
      slots.length !== rowCount * columnCount
    )
      return [];
    for (let r = 0; r < rowCount; r++)
      for (let c = 0; c < columnCount; c++) {
        const s = at(r, c),
          column = at(0, c),
          row = at(r, 0);
        if (
          !s ||
          !column ||
          !row ||
          s.key !== `s${shapeId}_r${r}_c${c}` ||
          Math.abs(s.x - column.x) > 0.1 ||
          Math.abs(s.w - column.w) > 0.1 ||
          Math.abs(s.y - row.y) > 0.1 ||
          Math.abs(s.h - row.h) > 0.1
        )
          return [];
      }
    const top = at(1, 0)!.y;
    const bodyHeight = Math.max(...slots.map((s) => s.y + s.h)) - top;
    const font = Math.max(
      ...slots.filter((s) => s.cell[0] > 0).map((s) => s.size),
    );
    return [
      {
        shapeId,
        columnCount,
        rowCount,
        bodyHeight,
        maxDataRows: Math.min(maxTableDataRows, Math.max(
          rowCount - 1, Math.floor(bodyHeight / (font * 2.5 + 8)), 1,
        )),
        headers: slots
          .filter((s) => s.cell[0] === 0)
          .sort((a, b) => a.cell[1] - b.cell[1])
          .map((s) => s.text),
      },
    ];
  });
}

/** Generated from the actual native grid, never a per-template schema. */
export function tablePlanJsonSchema(layout: Layout) {
  const tables=regularTables(layout);
  return {type:"object",additionalProperties:false,required:["tables"],properties:{tables:{
    type:"array",minItems:tables.length,maxItems:tables.length,
    items:{anyOf:tables.map(table=>({type:"object",additionalProperties:false,required:["shapeId","columns","rows"],properties:{
      shapeId:{type:"integer",enum:[table.shapeId]},
      columns:{type:"array",minItems:2,maxItems:table.columnCount,items:{type:"string"}},
      rows:{type:"array",minItems:1,maxItems:table.maxDataRows,description:"Each string describes ONE complete record, not individual cells or column values.",items:{type:"string"}},
    }}))},
  }}};
}

const tablePlansSchema = z.object({
  tables: z
    .array(
      z.object({
        shapeId: z.number().int(),
        columns: z.array(z.string().trim().min(1).max(120)),
        rows: z.array(z.string().trim().min(1).max(600)).min(1).max(maxTableDataRows),
      }),
    )
    .max(12),
});
export type TablePlans = z.infer<typeof tablePlansSchema>;
export function validateTablePlans(raw: unknown, layout: Layout): TablePlans {
  const result = tablePlansSchema.parse(raw),
    tables = regularTables(layout);
  if (
    result.tables.length !== tables.length ||
    new Set(result.tables.map((t) => t.shapeId)).size !== tables.length
  )
    throw new Error("Нужно описать каждую таблицу ровно один раз");
  for (const plan of result.tables) {
    const table = tables.find((t) => t.shapeId === plan.shapeId);
    if (
      !table ||
      plan.columns.length < 2 || plan.columns.length > table.columnCount ||
      plan.rows.length > table.maxDataRows
    )
      throw new Error(
        "Используй от двух до исходного числа столбцов и не более maxDataRows записей",
      );
    if (
      new Set(plan.rows.map((r) => r.toLowerCase())).size !== plan.rows.length
    )
      throw new Error("Каждая строка должна описывать отдельную запись");
  }
  return result;
}

/** Same grid policy as runtime.tables.expanded_table_layout: styled rows and
 * optional fewer columns, distributed inside the original table bounds. */
export function applyTablePlans(
  base: Layout,
  semantics: any,
  plans: TablePlans,
) {
  const layout = structuredClone(base),
    result = structuredClone(semantics);
  layout.tableRows = {};
  layout.tableColumns = {};
  layout.tableStructure = [];
  for (const plan of plans.tables) {
    const slots = base.slots.filter(
      (s) => s.shapeId === plan.shapeId && s.cell,
    );
    const cells = new Map(slots.map((s) => [s.cell.join(":"), s]));
    const rows = Math.max(...slots.map((s) => s.cell[0])) + 1;
    const top = cells.get("1:0")!.y;
    const height = Math.max(...slots.map((s) => s.y + s.h)) - top;
    const count = plan.rows.length;
    const originalColumns = Math.max(...slots.map(s => s.cell[1])) + 1;
    const left = cells.get('0:0')!.x;
    const width = Math.max(...slots.map(s=>s.x+s.w)) - left;
    const selectedWidths = plan.columns.map((_,c)=>cells.get(`0:${c}`)!.w);
    const scale = width / selectedWidths.reduce((sum,w)=>sum+w,0);
    const expanded: Slot[] = [];
    layout.tableRows[String(plan.shapeId)] = count;
    if (plan.columns.length !== originalColumns) layout.tableColumns[String(plan.shapeId)] = plan.columns.length;
    for (let r = 0; r <= count; r++)
      for (let c = 0; c < plan.columns.length; c++) {
        const exemplar =
          r === 0 ? 0 : r === count ? rows - 1 : 1 + ((r - 1) % (rows - 1));
        const source = cells.get(`${exemplar}:${c}`)!;
        const slot: Slot = {
          ...source,
          sourceFrameKey: source.key,
          cell: [r, c],
          key: `s${plan.shapeId}_r${r}_c${c}`,
          x: left + selectedWidths.slice(0,c).reduce((sum,w)=>sum+w,0)*scale,
          w: selectedWidths[c]*scale,
          ...(r
            ? { y: top + ((r - 1) * height) / count, h: height / count }
            : {}),
        };
        // Measured capacity belongs to the original height. It is invalid
        // after native rows are redistributed, even when the font is unchanged.
        if (Math.abs(slot.h - source.h) > 0.1 || Math.abs(slot.w-source.w)>0.1) delete slot.textFit;
        slot.maxChars = Math.max(
          8,
          Math.min(
            300,
            Math.floor(slot.w / Math.max(1, slot.size * 0.62)) *
              Math.max(1, Math.floor(slot.h / (slot.size * 1.35))),
          ),
        );
        expanded.push(slot);
      }
    const oldKeys = new Set(slots.map((s) => s.key));
    layout.slots = layout.slots
      .filter((s) => !oldKeys.has(s.key))
      .concat(expanded);
    result.fields = result.fields
      .filter((f: any) => !oldKeys.has(f.key))
      .concat(
        expanded.map((s) => ({
          key: s.key,
          role: "body",
          action: "replace",
          required: true,
          group: `table_${plan.shapeId}_row_${s.cell[0]}`,
          intent:
            s.cell[0] === 0
              ? `Заголовок столбца: ${plan.columns[s.cell[1]]}`
              : `Одна запись: ${plan.rows[s.cell[0] - 1]}. Свойство в этом столбце: ${plan.columns[s.cell[1]]}. Не перечисляй другие записи в этой ячейке.`,
        })),
      );
    layout.tableStructure.push({
      shapeId: plan.shapeId,
      columns: plan.columns,
      headerKeys: expanded.filter((s) => s.cell[0] === 0).map((s) => s.key),
      rows: plan.rows.map((record, i) => ({
        record,
        cellKeys: expanded.filter((s) => s.cell[0] === i + 1).map((s) => s.key),
      })),
    });
  }
  return { layout, semantics: result };
}

export function tableRowRepairIssues(issues: any[], layout: Layout) {
  const result = [...issues];
  // A semantic conflict can belong to the column heading, not the record.
  // Include that column and all headings immediately, while ordinary fit
  // repairs remain local to one record. This also works for merged grids.
  for (const issue of issues.filter(i => i.reason === "content_mismatch")) {
    const cell = layout.slots.find(s => s.key === issue.key && s.cell);
    if (!cell) continue;
    const table = layout.slots.filter(s => s.shapeId === cell.shapeId && s.cell);
    const firstRow = Math.min(...table.map(s => s.cell[0]));
    for (const slot of table.filter(s => s.cell[0] === firstRow || s.cell[1] === cell.cell[1])) {
      if (!result.some(i => i.key === slot.key)) result.push({
        key: slot.key,
        reason: "table_column_repair",
        message: "Проверь заголовок и значения столбца вместе. Можно уточнить заголовок под подтверждённые данные, сохранив смысл остальных строк. Не меняй корректные значения без необходимости.",
      });
    }
  }
  for (const table of layout.tableStructure || [])
    for (const row of table.rows) {
      if (
        !row.cellKeys.some((key: string) =>
          issues.some((issue) => issue.key === key),
        )
      )
        continue;
      for (const key of row.cellKeys)
        if (!result.some((issue) => issue.key === key))
          result.push({
            key,
            reason: "table_row_repair",
            message: `Исправляй запись целиком, сохраняя связь столбцов одной строки: ${row.record}`,
          });
    }
  return result;
}
