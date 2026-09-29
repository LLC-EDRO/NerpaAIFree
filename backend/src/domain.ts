import { z } from "zod";
import { alignmentSchema, applicableAlignment, alignmentIntent } from './text-alignment.js';
import { structuralPrefixes, preserveStructuralPrefix, structuralOrdinalMarkers } from "./structural-numbering.js";
import { tabularCellEvidence } from './tabular-evidence.js';
import { isLinkedUnitColumn } from './metric-unit-column.js';
import {
  visualDescriptionSchema,
  visualKinds,
  validateVisualDescriptions,
  type VisualPlan,
  type VisualSlot,
} from "./visual-types.js";
import { HttpError } from "./errors.js";
import type { PresentationResearch } from "./research.js";
export const briefSchema = z.object({
  topic: z.string().trim().min(3).max(1500),
  count: z.number().int().min(1).max(40),
  sourceText: z.string().max(60000).default(""),
  webSearch: z.boolean().default(false),
});
export const planSchema = z.object({
  slides: z
    .array(
      z.object({
        id: z.string().optional(),
        sourceLayoutId: z.string(),
        title: z.string().trim().min(1).max(200),
        brief: z.string().trim().min(1).max(2000),
        images: z.array(z.object({
          slotIndex: z.number().int().min(0).max(79),
          kind: z.enum(visualKinds),
          background: z.enum(['transparent', 'opaque']),
          description: z.string().trim().min(1).max(700),
        })).max(80).optional().catch(undefined),
      }),
    )
    .min(1)
    .max(40),
});
export type Outline = z.infer<typeof planSchema>;
export type Brief = z.infer<typeof briefSchema> & {instructions?:string};
export interface Layout {
  id: string;
  index: number;
  name: string;
  usable: boolean;
  warnings: string[];
  slots: Slot[];
  charts: any[];
  visualSlots?: VisualSlot[];
  [key: string]: any;
}
export interface Slot {
  key: string;
  text: string;
  shapeId: number;
  role: string;
  maxChars: number;
  size: number;
  x: number;
  y: number;
  w: number;
  h: number;
  [key: string]: any;
}
export interface Project {
  modelSettings?: import("./model-settings.js").ModelSelection;
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  revision: number;
  status: string;
  message: string;
  busy: boolean;
  progress?: { done: number; total: number };
  brief?: Brief;
  research?: PresentationResearch;
  researchWarning?: string;
  planWarnings?: Array<{slide:number;reason:string;message:string}>;
  analysis?: any;
  plan?: Outline;
  planAdaptation?: { version: 1; baseline: Outline; warnings?: Array<{slide:number;reason:string;message:string}> };
  contract?: any;
  result?: {
    warnings?: Array<{slide?: number; key?: string; reason: string; message: string}>;
    pdfAvailable?: boolean;
    slides: number;
    expandedFields?: number;
    rebuiltSlides?: Array<{ slide: number; reason: string }>;
  };
  visuals?: VisualPlan;
  error?: { code: string; message: string; details?: unknown };
  lastAction?: string;
  events: Array<{ at: string; message: string }>;
}
export const semanticsSchema = z.object({
  images: z.array(visualDescriptionSchema).optional(),
  composition: z.string().max(3000),
  layoutIssues: z.array(z.object({keys:z.array(z.string()).min(2).max(12),instruction:z.string().min(5).max(600)})).max(15).optional().catch(undefined),
  fields: z.array(
    z.object({
      key: z.string(),
      role: z.enum([
        "title",
        "body",
        "metric",
        "metric_label",
        "unit",
        "brand",
        "decoration",
        "page_number",
        "step_number",
        "footer",
      ]),
      group: z.string().max(100),
      intent: z.string().max(600),
      action: z.enum(["replace", "preserve", "clear"]),
      required: z.boolean(),
      alignment: alignmentSchema.optional().catch(undefined),
    }),
  ),
  charts: z
    .array(z.object({ key: z.string(), intent: z.string().max(1000) }))
    .default([]),
});
export const copySchema = z.object({
  fields: z.record(
    z.string(),
    z.object({
      text: z.string().max(4000),
      evidence: z.array(z.string().max(3000)).default([]),
    }),
  ),
  charts: z.preprocess(
    (value) => (Array.isArray(value) && value.length === 0 ? {} : value),
    z
      .record(
        z.string(),
        z.object({
          title: z.string().max(80),
          categories: z.array(z.string().min(1).max(30)),
          series: z.array(
            z.object({
              name: z.string().min(1).max(40),
              values: z.array(z.number().finite()),
            }),
          ),
          evidence: z.array(z.string().max(3000)),
        }),
      )
      .default({}),
  ),
});
export function exactKeys(actual: string[], expected: string[], label: string) {
  if (
    actual.length !== expected.length ||
    new Set(actual).size !== actual.length ||
    actual.some((k) => !expected.includes(k))
  )
    throw new HttpError(
      422,
      `Неполное сопоставление полей: ${label}`,
      "field_mapping",
      {
        expected,
        actual,
        missing: expected.filter((k) => !actual.includes(k)),
        unexpected: actual.filter((k) => !expected.includes(k)),
      },
    );
}
export function validateSemantics(value: unknown, layout: Layout) {
  const result = semanticsSchema.parse(value);
  if (layout.visualSlots?.length)
    result.images = validateVisualDescriptions(
      result.images,
      layout.visualSlots,
      layout.slots.map((s) => s.key),
    );
  exactKeys(
    result.fields.map((f) => f.key),
    layout.slots.map((s) => s.key),
    "text",
  );
  exactKeys(
    result.charts.map((f) => f.key),
    layout.charts.map((s) => s.key),
    "charts",
  );
  for (const field of result.fields) {
    const slot = layout.slots.find((s) => s.key === field.key)!;
    if (
      (field.action !== "clear" || field.required) &&
      layout.visualSlots?.some((v) =>
        v.detection?.markerFieldKeys.includes(field.key),
      )
    ) {
      field.action = "clear";
      field.required = false;
      field.role = "decoration";
      field.intent =
        "Служебная инструкция места изображения. Убрать текст, не превращать в подпись.";
    }
    // Table cells describe properties of a row, not mandatory hero metrics.
    // Regular tables already use body in applyTablePlans; merged grids must
    // follow the same rule. Numbers remain evidence-checked, and the reviewer
    // verifies column meaning. A sample risk score must not force invented
    // probabilities in a new topic.
    if (slot.cell && field.role === "metric") {
      field.role = "body";
      field.intent +=
        " Ячейка таблицы: тип значения определяется итоговым заголовком столбца и смыслом строки, а не цифрой образца.";
    }
    if (
      field.role === "metric" &&
      /^0?\d{1,2}$/.test(slot.text) &&
      /порядков|номер.*шага/i.test(field.intent)
    )
      field.role = "step_number";
    if (
      field.action === "preserve" &&
      !["brand", "decoration", "page_number"].includes(field.role)
    )
      field.action = "replace";
    // Position is only a parser hint. Ordinary footer wording is content;
    // the model can explicitly identify a real brand instead of decoration.
    if (slot.role === "footer" && field.role === "decoration" &&
        /[\p{L}\p{N}]/u.test(slot.text) && field.action === "preserve") {
      field.role = "footer";
      field.action = "replace";
    }
    if (field.required && field.action === "clear")
      throw new HttpError(
        422,
        "Обязательное поле нельзя очистить",
        "semantic_required",
      );
  }
  return result;
}
// Strip citation presentation syntax only when the remaining quotation is
// literally present in the supplied source; never infer evidence from numbers.
export function sourceQuotation(value: string, source: string) {
  if (source.includes(value)) return value;
  let quote = value
    .replace(/^(?:userSource|userMaterials|sourceText)\s*:\s*/i, "")
    .trim();
  if (
    (quote.startsWith("«") && quote.endsWith("»")) ||
    (quote.startsWith('"') && quote.endsWith('"')) ||
    (quote.startsWith("“") && quote.endsWith("”"))
  )
    quote = quote.slice(1, -1).trim();
  return quote && source.includes(quote) ? quote : value;
}
export function numbers(text: string) {
  const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
  const dates: string[] = [];
  text = text.replace(new RegExp(`(\\d{1,2})\\s+(${months.join('|')})\\s+(\\d{4})`, 'giu'),
    (_, day, month, year) => `${day}.${months.indexOf(month.toLowerCase())+1}.${year}`);
  text = text.replace(/\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b/g, (value, d, m, y) => {
    const date = new Date(Date.UTC(+y,+m-1,+d));
    if (date.getUTCFullYear()!==+y || date.getUTCMonth()!==+m-1 || date.getUTCDate()!==+d) return value;
    dates.push(`date:${y}-${String(+m).padStart(2,'0')}-${String(+d).padStart(2,'0')}`);
    return ' ';
  });
  // A run with several commas is a list, not pairs of decimal fractions.
  // A single decimal comma remains intact. Do not infer a magnitude from units.
  const normalized = text.replace(/\d+(?:,\d+){2,}/g, value => value.replace(/,/g, ';')).replace(
    /\b\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?\b/g,
    (value) => value.replace(/[ \u00a0\u202f]/g, ""),
  );
  return dates.concat([...normalized.matchAll(/[+−-]?\d+(?:[.,]\d+)?(?:\s*\/\s*\d+)?/g)].map(m => {
    let value = m[0].replace(/\s/g, '').replace(',', '.').replace('−', '-');
    const before = normalized.slice(0, m.index);
    // A hyphen in an identifier or numeric range is not a negative sign.
    if (value.startsWith('-') && m[0][0] === '-' && /[\p{L}\d_]\s*$/u.test(before)) value = value.slice(1);
    return value.split('/').map(part => {
      const canonical = part.replace(/^\+/, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
      return canonical === '-0' ? '0' : canonical;
    }).join('/');
  }));
}
export function planContentNumbers(text: string, evidenceIds: string[]) {
  const known = new Set(evidenceIds.map(id => id.toLowerCase()));
  return numbers(
    text
      // Named document labels are structural, even when step 1 is on slide 8.
      // Require a caption separator; leave quantities and inner facts intact.
      .replace(/(^|\n)(\s*(?:этап|шаг|пункт|слайд|иллюстрация|рисунок|таблица|раздел|глава|step|stage|slide|illustration|figure|table|section|chapter)\s+№?\s*)\d{1,3}(?=\s*[:—–.)-](?:\s|$))/giu, '$1$2')
      .replace(/\bevidence-\d+\b/gi, (id) => (known.has(id.toLowerCase()) ? "" : id)),
  );
}
// Evidence can spell small counts out; keep these separate from display values.
// Skip composite numerals instead of accidentally treating "двадцать один" as 1.
export function evidenceNumbers(text: string) {
  const small: Record<string, string> = {
    ноль: "0",
    нуля: "0",
    один: "1",
    одна: "1",
    одно: "1",
    одного: "1",
    одной: "1",
    одному: "1",
    одним: "1",
    одну: "1",
    два: "2",
    две: "2",
    двух: "2",
    двум: "2",
    двумя: "2",
    три: "3",
    трех: "3",
    трем: "3",
    тремя: "3",
    четыре: "4",
    четырех: "4",
    четырем: "4",
    четырьмя: "4",
    пять: "5",
    пяти: "5",
    шесть: "6",
    шести: "6",
    семь: "7",
    семи: "7",
    восемь: "8",
    восьми: "8",
    девять: "9",
    девяти: "9",
    десять: "10",
    десяти: "10",
  };
  const tokens =
    text
      .toLowerCase()
      .replace(/ё/g, "е")
      .match(/[\p{L}\d]+|[^\p{L}\d\s]+/gu) || [];
  const spelled = tokens.flatMap((word, i) => {
    const compound =
      /^(одно|двух|трех|четырех|пяти|шести|семи|восьми|девяти|десяти)(?:дневн|летн|годичн|месячн|недельн|часов|минутн|километров|процентн)\p{L}*$/u.exec(
        word,
      );
    const value = small[word] ?? (compound ? small[compound[1]] : undefined);
    if (value === undefined) return [];
    // Check numeral neighbours using complete known numeral words/stems, not nouns.
    const adjacent = [tokens[i - 1], tokens[i + 1]].filter(Boolean);
    if (
      adjacent.some((w) =>
        /^(?:\d+|один|одна|одно|одного|одной|два|две|двух|три|трех|четыре|пять|шесть|семь|восемь|девять|десять|\p{L}*надцать|двадцать|тридцать|сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяносто|сто|двести|триста|четыреста|пятьсот|шестьсот|семьсот|восемьсот|девятьсот|тысяч\p{L}*|миллион\p{L}*|миллиард\p{L}*)$/u.test(
          w,
        ),
      )
    )
      return [];
    return [value];
  });
  const fractions = [
    ...text
      .toLowerCase()
      .replace(/ё/g, "е")
      .matchAll(
        /(?<!\p{L})(?:(одна|одну|одной|две|двух|два|три|трех|\d+)\s+)?(половина|половину|половины|треть|трети|третьих|четверть|четверти|четвертых)(?!\p{L})/gu,
      ),
  ].map(
    (m) =>
      `${m[1] ? small[m[1]] || m[1] : "1"}/${m[2].startsWith("полов") ? 2 : m[2].startsWith("трет") ? 3 : 4}`,
  );
  const values = numbers(text);
  // A full date also supports displaying its year/day separately; a year alone
  // cannot support an invented full date.
  const dateParts = values.filter(v=>v.startsWith('date:')).flatMap(v=>{
    const [year,,day] = v.slice(5).split('-'); return [year,String(Number(day))];
  });
  return [...values, ...dateParts, ...spelled, ...fractions];
}
// A sample speaker signature is not a requirement for the new presentation.
// Apply this to saved contracts too, without touching native geometry or keys.
export function bindSemanticsToPlan(
  raw: unknown,
  layout: Layout,
  approved: Outline["slides"][number],
) {
  const semantics = validateSemantics(raw, layout);
  for (const field of semantics.fields) {
    if (
      !["body", "footer"].includes(field.role) ||
      field.action !== "replace" ||
      !/спикер|должност|регали|имя.*фамил|speaker|presenter/i.test(field.intent)
    )
      continue;
    if (/подзаголов/i.test(approved.brief)) {
      field.intent =
        "Краткий тематический подзаголовок по утверждённому плану. Не требует имени или должности спикера.";
      field.group = "subtitle";
      field.required = true;
    } else if (/пояснен|подзаголов/i.test(field.intent)) {
      field.intent =
        "Необязательная краткая подпись или пояснение по утверждённому плану. Имя и должность допустимы только при наличии подтверждённых сведений пользователя; иначе не придумывать спикера.";
      field.required = false;
    } else {
      field.intent =
        "Необязательная подпись спикера: только подтверждённые пользователем имя и должность. Если сведений нет, оставить пустым.";
      field.required = false;
    }
  }
  return semantics;
}
export function extractInlineEvidence(
  text: string,
  known: Map<string, string>,
) {
  const evidence: string[] = [];
  const cleaned = text.replace(
    /\[\s*evidence-\d+(?:\s*[,;]\s*evidence-\d+)*\s*\]/g,
    (marker) => {
      const ids = marker.match(/evidence-\d+/g)!;
      if (ids.some((id) => !known.has(id))) return marker;
      evidence.push(...ids.map((id) => known.get(id)!));
      return "";
    },
  );
  return {
    text: cleaned === text ? text : cleaned.replace(/[ \t]+\n/g, "\n").trim(),
    evidence,
  };
}

export function validateCopy(
  raw: unknown,
  layout: Layout,
  semantics: z.infer<typeof semanticsSchema>,
  source: string,
  evidencePool: { id: string; text: string; sourceIds?: string[] }[] = [],
  nativeLists: Record<string, string[]> = {},
) {
  const data = copySchema.parse(raw);
  const sourceMarkers = structuralOrdinalMarkers(layout.slots.map(s => ({...s,role:semantics.fields.find(f=>f.key===s.key)?.role || s.role})));
  for (const [key,text] of sourceMarkers) {
    if (data.fields[key] && semantics.fields.find(f=>f.key===key)?.action === 'replace')
      data.fields[key] = {text,evidence:[]};
  }
  const sourcePrefixes = structuralPrefixes(layout.slots.map(s => ({...s,role:semantics.fields.find(f=>f.key===s.key)?.role || s.role})));
  for (const [key,prefix] of sourcePrefixes) {
    const field=semantics.fields.find(f=>f.key===key);
    if (data.fields[key]?.text.trim() && field?.action==='replace') data.fields[key].text=preserveStructuralPrefix(data.fields[key].text,prefix);
  }
  // The model can cite a retrieved fact by ID instead of recopying a long quote.
  // Resolve only IDs from this project's current research, then validate normally.
  const knownEvidence = new Map(evidencePool.map((e) => [e.id, e.text]));
  for (const value of [
    ...Object.values(data.fields),
    ...Object.values(data.charts),
  ]) {
    value.evidence = value.evidence.map((e) =>
      sourceQuotation(knownEvidence.get(e) ?? e, source),
    );
  }
  exactKeys(
    Object.keys(data.fields),
    layout.slots.map((s) => s.key),
    "text",
  );
  exactKeys(
    Object.keys(data.charts),
    layout.charts.map((s) => s.key),
    "charts",
  );
  const issues: any[] = [];
  const citationWarnings: any[] = [];
  // A numbered native table column is document structure, not a statistic.
  // Require both the native and generated heading, and the exact row ordinal;
  // a year/score/percentage in any other column still needs evidence.
  const numberHeading = (text: string) =>
    /^(?:№(?:\s*п\.?\s*\/\s*п\.?)?|#|no\.?|номер)$/iu.test(text.trim());
  const ordinalKeys = new Set<string>(sourceMarkers.keys());
  const headingPrefixes = structuralPrefixes(layout.slots.map(slot => ({
    key: slot.key, text: data.fields[slot.key].text, cell: slot.cell,
    role: semantics.fields.find(f => f.key === slot.key)?.role,
  })));
  for (const header of layout.slots.filter(
    (s) =>
      s.cell && numberHeading(s.text) && numberHeading(data.fields[s.key].text),
  )) {
    const cells = layout.slots
      .filter(
        (s) =>
          s.cell &&
          s.shapeId === header.shapeId &&
          s.cell[1] === header.cell[1] &&
          s.cell[0] > header.cell[0],
      )
      .sort((a, b) => a.cell[0] - b.cell[0]);
    for (const [index, cell] of cells.entries()) {
      const value = data.fields[cell.key],
        ordinal = String(index + 1);
      // Superscript/circled compatibility digits are not a fit workaround.
      // Normalize only an exact structural ordinal, never arbitrary metrics.
      if (value.text.trim().normalize("NFKC") === ordinal) {
        if (
          semantics.fields.find((f) => f.key === cell.key)?.action === "replace"
        )
          value.text = ordinal;
        ordinalKeys.add(cell.key);
      }
    }
  }
  for (const f of semantics.fields) {
    const value = data.fields[f.key],
      slot = layout.slots.find((s) => s.key === f.key)!;
    if (f.action === "preserve") {
      value.text = slot.text;
      value.evidence = [];
      continue;
    }
    if (f.action === "clear") {
      value.text = "";
      value.evidence = [];
      continue;
    }
    const inline = extractInlineEvidence(value.text, knownEvidence);
    value.text = inline.text;
    value.evidence = [...new Set([...value.evidence, ...inline.evidence])];
    // A table record can cite the year/context in another cell of the SAME
    // row. Complete only missing numeric citations from the SAME source URL
    // identity; never borrow evidence from another row or invent a new fact.
    if (slot.cell) {
      const factsByText = new Map(evidencePool.map((e) => [e.text, e]));
      const sourceIds = new Set(
        value.evidence.flatMap((q) => factsByText.get(q)?.sourceIds || []),
      );
      const missing = numbers(value.text).filter(
        (n) => !evidenceNumbers(value.evidence.join(" ")).includes(n),
      );
      if (missing.length && sourceIds.size) {
        const related = layout.slots
          .filter(
            (s) =>
              s.cell &&
              s.shapeId === slot.shapeId &&
              s.cell[0] === slot.cell[0] &&
              s.key !== slot.key,
          )
          .flatMap((s) => data.fields[s.key].evidence)
          .map((q) => factsByText.get(q))
          .filter(
            (e) =>
              e &&
              source.includes(e.text) &&
              e.sourceIds?.some((id) => sourceIds.has(id)) &&
              missing.some((n) => evidenceNumbers(e.text).includes(n)),
          );
        value.evidence = [
          ...new Set([...value.evidence, ...related.map((e) => e!.text)]),
        ];
      }
    }
    if (nativeLists[f.key]?.length) {
      value.text = value.text
        .split("\n")
        .map((line) => {
          const prefix = line.match(/^\s*(\S)\s+/u);
          return prefix && nativeLists[f.key].includes(prefix[1])
            ? line.slice(prefix[0].length)
            : line;
        })
        .join("\n");
    }
    if (f.required && !value.text.trim())
      issues.push({ key: f.key, reason: "required_text" });
    if (
      f.role === "metric" &&
      !isLinkedUnitColumn(f.key,layout.slots,semantics.fields,data) &&
      (!numbers(value.text).length ||
        /(?:нет|отсутствуют)\s+данн|данн[а-яё]*\s+(?:нет|отсутствуют|не\s+(?:найдены|предоставлены|подтверждены))/iu.test(
          value.text,
        ))
    )
      issues.push({
        key: f.key,
        reason: "metric_fact_required",
        message:
          "Метрика требует подтверждённого числового значения и соответствующей подписи. Выбери подходящий факт из researchEvidence или материалов пользователя. Тире, пустое поле и «нет данных» недопустимы.",
      });
    if (
      f.required &&
      /^(?:[—–\-?…]+|н\/?д|n\/?a|нет данных|данных нет|данные не предоставлены)[.!\s]*$/iu.test(
        value.text.trim(),
      )
    )
      issues.push({
        key: f.key,
        reason: "placeholder_content",
        message:
          "Обязательное содержательное поле нельзя заполнить заглушкой. Нужен факт или содержательный тезис по теме.",
      });
    const evidence = value.evidence.filter(
      (q) => q.trim() && source.includes(q),
    );
    if (value.evidence.length !== evidence.length)
      citationWarnings.push({
        key: f.key,
        reason: "evidence_not_in_source",
        invalidEvidence: value.evidence.filter((q) => !evidence.includes(q)),
        message:
          "Используй точную цитату или ID из researchEvidence, а не пересказ источника.",
      });
    // Invalid citation text is not evidence. Removing it does not approve a
    // claim: every number below still needs valid support, and the independent
    // semantic review remains mandatory for factual non-numeric statements.
    value.evidence = evidence;
    const ordinal =
      ordinalKeys.has(f.key) ||
      (["page_number", "step_number"].includes(f.role) &&
        /^0?\d{1,2}$/.test(value.text) &&
        Number(value.text) >= 1 &&
        Number(value.text) <= 20);
    // Only the server may mark a verified contiguous numbered heading set.
    // Numbers inside the actual sentence still require evidence.
    const prefix = headingPrefixes.get(f.key);
    const factualText = prefix ? value.text.slice(prefix.length) : value.text;
    const cellEvidence=tabularCellEvidence(slot,layout.slots,data.fields,evidence);
    const supportedNumbers=[...evidenceNumbers(evidence.join(' ')),...(cellEvidence ? evidenceNumbers(cellEvidence) : [])];
    if (
      !ordinal &&
      numbers(factualText).some(
        (n) => !supportedNumbers.includes(n),
      )
    ) {
      const unsupportedNumbers = [
        ...new Set(
          numbers(factualText).filter(
            (n) => !supportedNumbers.includes(n),
          ),
        ),
      ];
      issues.push({
        key: f.key,
        reason: "unsupported_number",
        text: value.text,
        unsupportedNumbers,
        suggestedEvidence: unsupportedNumbers.map((number) => ({
          number,
          candidates: evidencePool
            .filter(
              (e) =>
                source.includes(e.text) &&
                evidenceNumbers(e.text).includes(number),
            )
            .map((e) => ({ id: e.id, text: e.text })),
        })),
        message:
          "Для перечисленных чисел отсутствует подтверждение в evidence этого поля. Добавь соответствующий ID из suggestedEvidence, если он подтверждает тот же факт, или убери неподтверждённое число. Сам текст может быть корректным: проверь полноту ссылок.",
      });
    }
  }
  for (const spec of layout.charts) {
    const chart = data.charts[spec.key];
    if (
      chart.categories.length !== spec.pointCount ||
      chart.series.length !== spec.seriesCount ||
      chart.series.some((s) => s.values.length !== spec.pointCount)
    )
      issues.push({ key: spec.key, reason: "chart_dimensions" });
    const evidence = chart.evidence
      .filter((q) => q.trim() && source.includes(q))
      .join(" ");
    if (
      chart.evidence.some((q) => !source.includes(q)) ||
      chart.series
        .flatMap((s) => s.values)
        .some((n) => !evidenceNumbers(evidence).includes(String(n)))
    )
      issues.push({ key: spec.key, reason: "chart_evidence" });
    if (
      /pie|doughnut/.test(spec.type) &&
      chart.series.some(
        (s) => s.values.some((n) => n < 0) || !s.values.some((n) => n > 0),
      )
    )
      issues.push({ key: spec.key, reason: "chart_range" });
  }
  return { data, issues, citationWarnings };
}
export function nativeSlide(
  data: z.infer<typeof copySchema>,
  layout: Layout,
  title: string,
  ordinal: number,
  sourceOrdinal?: number,
) {
  return {
    title,
    native: {
      sourceSlideId: layout.id,
      mode: "source",
      preserveTemplate: true,
      safeTextExpansion: true,
      alignmentIntent: Object.fromEntries(layout.slots.flatMap(s => {
        const a=alignmentIntent(layout.textAlignment?.[s.key],s,data.fields[s.key]?.text || '');
        return a ? [[s.key,a]] : [];
      })),
      textAlignment: Object.fromEntries(layout.slots.flatMap(s => {
        const a=applicableAlignment(layout.textAlignment?.[s.key],s,data.fields[s.key]?.text || '');
        return a ? [[s.key,a]] : [];
      })),
      ...(layout.geometryRepairs
        ? { geometryRepairs: layout.geometryRepairs }
        : {}),
      ordinal,
      sourceOrdinal,
      ...(layout.tableRows ? { tableRows: layout.tableRows } : {}),
      ...(layout.tableColumns ? { tableColumns: layout.tableColumns } : {}),
      fields: Object.fromEntries(
        Object.entries(data.fields).map(([key, v]) => [key, v.text]),
      ),
      charts: Object.fromEntries(
        Object.entries(data.charts).map(([key, { evidence, ...v }]) => [
          key,
          v,
        ]),
      ),
    },
  };
}
