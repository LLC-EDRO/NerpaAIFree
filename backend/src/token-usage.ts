import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import {
  receiptCost,
  emptyCost,
  addCost,
  pricing,
  type CostCounts,
} from "./usage-cost.js";

const stageNames = {
  parse: "Разбор шаблона",
  search: "Поиск источников",
  plan: "Генерация плана",
  describe: "Описание шаблона",
  fill: "Заполнение слайдов",
  review: "Проверка содержания",
  images: "Генерация изображений",
  imagePlanning: "Подготовка изображений",
  export: "Сборка и проверка PPTX/PDF",
  other: "Другие запросы",
};
type Stage = keyof typeof stageNames;
export type TokenCounts = {
  inputTokens: number;
  outputTokens: number;
  requests: number;
  missingInput: number;
  missingOutput: number;
};
const empty = (): TokenCounts => ({
  inputTokens: 0,
  outputTokens: 0,
  requests: 0,
  missingInput: 0,
  missingOutput: 0,
});
const number = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : undefined;
export function normalizeUsage(record: any): {
  stage: Stage;
  counts: TokenCounts;
  cost: CostCounts;
} {
  const name = typeof record?.stage === "string" ? record.stage : "";
  const stage: Stage =
    name === "image-generation"
      ? "images"
      : /^visual-/.test(name)
        ? "imagePlanning"
        : name === "web-search" || name === "research-coverage"
          ? "search"
          : name === "plan" || name.startsWith("plan-adapt-")
            ? "plan"
            : /^describe-/.test(name)
              ? "describe"
              : /^fill-/.test(name)
                ? "fill"
                : /^content-review-/.test(name)
                  ? "review"
                  : "other";
  const usage = record?.usage ?? record?.response?.usage;
  // Both providers report cached input and reasoning inside the parent totals.
  // Never add usage detail counters or total_tokens a second time.
  const input = number(usage?.input_tokens) ?? number(usage?.prompt_tokens);
  const output =
    number(usage?.output_tokens) ?? number(usage?.completion_tokens);
  return {
    stage,
    cost: receiptCost(record),
    counts: {
      inputTokens: input ?? 0,
      outputTokens: output ?? 0,
      requests: 1,
      missingInput: input === undefined ? 1 : 0,
      missingOutput: output === undefined ? 1 : 0,
    },
  };
}
type Entry = ReturnType<typeof normalizeUsage>;
export function summarizeUsage(entries: Entry[]) {
  const rows = Object.entries(stageNames).map(([id, label]) => ({
    id,
    label,
    ...empty(),
    cost: emptyCost(),
  }));
  const total = empty();
  const cost = emptyCost();
  for (const entry of entries) {
    const row = rows.find((r) => r.id === entry.stage)!;
    addCost(row.cost, entry.cost);
    addCost(cost, entry.cost);
    for (const key of Object.keys(total) as (keyof TokenCounts)[]) {
      row[key] += entry.counts[key];
      total[key] += entry.counts[key];
    }
  }
  return {
    total,
    cost,
    pricing,
    stages: rows.filter((r) => r.id !== "other" || r.requests > 0),
  };
}
// Read immutable provider receipts, not project.json: concurrent checkpoints
// cannot overwrite counters, and opening/downloading never increments them.
const cache = new Map<
  string,
  Map<string, { signature: string; entry: Entry }>
>();
export async function projectTokenUsage(folder: string) {
  const directory = join(folder, "llm");
  let names: string[];
  try {
    names = (await readdir(directory)).filter((n) => n.endsWith(".json"));
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT")
      return summarizeUsage([]);
    throw e;
  }
  if (!cache.has(folder)) {
    if (cache.size >= 32) cache.delete(cache.keys().next().value!);
    cache.set(folder, new Map());
  }
  const receipts = cache.get(folder)!;
  const entries = await Promise.all(
    names.map(async (name) => {
      const path = join(directory, name),
        info = await stat(path);
      const signature = `${info.mtimeMs}:${info.size}`;
      const saved = receipts.get(name);
      if (saved?.signature === signature) return saved.entry;
      let record: unknown;
      try {
        record = JSON.parse(await readFile(path, "utf8"));
      } catch {
        record = null;
      } // A damaged receipt is unknown consumption, not zero.
      const entry = normalizeUsage(record);
      receipts.set(name, { signature, entry });
      return entry;
    }),
  );
  for (const name of receipts.keys())
    if (!names.includes(name)) receipts.delete(name);
  return summarizeUsage(entries);
}
