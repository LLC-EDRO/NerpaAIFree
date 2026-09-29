import { AsyncLocalStorage } from "node:async_hooks";
import { dirname, join } from "node:path";
import { z } from "zod";
import { dataRoot, readJson, writeJson } from "./store.js";
import { HttpError } from "./errors.js";
import { demoModels, eligibleModel, type AvailableModel } from "./model-policy.js";

const selectionSchema = z.object({
  provider: z.enum(["demo", "openwebui"]).default("demo"),
  baseUrl: z.string().default("http://127.0.0.1:3000").transform((value) => {
    const url = new URL(value.trim());
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new HttpError(400, "Укажите HTTP(S) адрес без логина, параметров и фрагмента", "model_url");
    return url.toString().replace(/\/+$/, "").replace(/\/api$/, "");
  }),
  textModel: z.string().trim().max(300).default(""),
  visionModel: z.string().trim().max(300).default(""),
});
export type ModelSelection = z.infer<typeof selectionSchema>;
type Settings = ModelSelection & { apiKey: string; approved: AvailableModel[] };
const path = join(dirname(dataRoot), "model-settings.json");
const emptySettings = (): Settings => ({ ...selectionSchema.parse({}), apiKey: "", approved: [] });
let settings = emptySettings();
const context = new AsyncLocalStorage<ModelSelection>();
export const currentSelection = (): ModelSelection => {
  const { provider, baseUrl, textModel, visionModel } = settings;
  return { provider, baseUrl, textModel, visionModel };
};
export const activeSelection = () => context.getStore() || currentSelection();
export const withModelSelection = <T>(selection: ModelSelection, fn: () => T) => context.run(selection, fn);
export const defaultSelection = () => selectionSchema.parse({});
export function publicModelSettings() {
  return { ...currentSelection(), keyConfigured: !!settings.apiKey, examples: demoModels };
}
export async function loadModelSettings() {
  try {
    const saved = await readJson(path);
    const selection = selectionSchema.parse(saved);
    const approved = z.array(z.object({
      id: z.string(), name: z.string(), sizeB: z.number(), license: z.string(),
      text: z.boolean(), vision: z.boolean(),
    })).parse(saved.approved);
    if (selection.provider === "openwebui" &&
      approved.some((m) => m.id === selection.textModel && m.text && m.sizeB <= 27) &&
      approved.some((m) => m.id === selection.visionModel && m.vision && m.sizeB <= 20)) {
      settings = { ...selection, apiKey: z.string().max(8192).parse(saved.apiKey || ""), approved };
      return;
    }
    // Old cloud/provider settings cannot silently activate in the public lab.
    settings = emptySettings();
    await writeJson(path, settings);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      settings = emptySettings();
      await writeJson(path, settings);
    }
  }
}
export function connectionFor(selection: ModelSelection) {
  if (selection.provider !== "openwebui")
    throw new HttpError(503, "Подключите собственный сервер моделей в настройках.", "model_not_configured");
  if (selection.baseUrl !== settings.baseUrl || selection.textModel !== settings.textModel || selection.visionModel !== settings.visionModel)
    throw new HttpError(409, "Настройки моделей изменились. Создайте новый эксперимент с текущими моделями.", "model_connection_changed");
  if (!settings.approved.some((m) => m.id === selection.textModel && m.text && m.sizeB <= 27) ||
      !settings.approved.some((m) => m.id === selection.visionModel && m.vision && m.sizeB <= 20))
    throw new HttpError(400, "Выберите разрешённые текстовую и визуальную модели.", "model_unavailable");
  return { baseUrl: selection.baseUrl, apiKey: settings.apiKey };
}
const inputSchema = z.object({
  provider: z.enum(["demo", "openwebui"]), baseUrl: z.string(),
  textModel: z.string(), visionModel: z.string(),
  apiKey: z.string().trim().max(8192).optional(), clearApiKey: z.boolean().optional(),
});
function draft(raw: unknown) {
  const value = inputSchema.parse(raw);
  const selection = selectionSchema.parse(value);
  const apiKey = value.clearApiKey || selection.provider === "demo" ? "" :
    value.apiKey || (selection.baseUrl === settings.baseUrl ? settings.apiKey : "");
  return { ...selection, apiKey };
}
export async function discoverModels(raw: unknown, fetcher: typeof fetch = fetch) {
  const config = draft(raw);
  try {
    const response = await fetcher(config.baseUrl + "/api/models", {
      headers: config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {},
      signal: AbortSignal.timeout(20000), redirect: "error",
    });
    if (!response.ok)
      throw new HttpError(502, response.status === 401 || response.status === 403 ?
        "Сервер моделей требует действующий ключ доступа." :
        `Сервер моделей вернул HTTP ${response.status}`, "model_catalog_failed");
    const body = await response.json();
    if (!Array.isArray(body.data)) throw new Error("invalid_catalog");
    return [...new Map<string, AvailableModel>(body.data.map(eligibleModel).filter(Boolean).map((m: AvailableModel) => [m.id, m])).values()];
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, "Не удалось загрузить модели. Проверьте адрес и доступность вашего сервера.", "model_catalog_unavailable");
  }
}
export async function saveModelSettings(raw: unknown) {
  const next = draft(raw);
  let approved: AvailableModel[] = [];
  if (next.provider === "openwebui") {
    if (!next.textModel || !next.visionModel)
      throw new HttpError(400, "Выберите текстовую и визуальную модели.", "model_required");
    approved = await discoverModels({ ...next, clearApiKey: !next.apiKey });
    if (!approved.some((m) => m.id === next.textModel && m.text) ||
        !approved.some((m) => m.id === next.visionModel && m.vision))
      throw new HttpError(400, "Модель недоступна либо не соответствует открытой лицензии и ограничению 27B/20B.", "model_unavailable");
  }
  settings = { ...next, approved: approved.filter((m) => m.id === next.textModel || m.id === next.visionModel) };
  await writeJson(path, settings);
  return publicModelSettings();
}
