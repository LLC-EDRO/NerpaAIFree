import { AsyncLocalStorage } from "node:async_hooks";
import { dirname, join } from "node:path";
import { z } from "zod";
import { dataRoot, readJson, writeJson } from "./store.js";
import { HttpError } from "./errors.js";
import { eligibleModel, type AvailableModel } from "./model-policy.js";

const selectionSchema = z.object({
  provider: z.literal("ollama").default("ollama"),
  baseUrl: z.string().default(process.env.LOCAL_MODEL_URL || "http://127.0.0.1:11434").transform((value) => {
    const url = new URL(value.trim());
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname) || url.username || url.password || url.search || url.hash)
      throw new HttpError(400, "Укажите локальный HTTP-адрес Ollama", "model_url");
    return url.toString().replace(/\/+$/, "");
  }),
  textModel: z.string().trim().max(300).default(process.env.LOCAL_TEXT_MODEL || "gemma4:12b"),
  visionModel: z.string().trim().max(300).default(process.env.LOCAL_VISION_MODEL || "gemma4:12b"),
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
  return { ...currentSelection(), keyConfigured: false, imageModel: process.env.LOCAL_IMAGE_MODEL || "FLUX.2-klein-4B" };
}
export async function loadModelSettings() {
  try {
    const saved = await readJson(path);
    const selection = selectionSchema.parse(saved);
    const approved = z.array(z.object({
      id: z.string(), name: z.string(), sizeB: z.number(), license: z.string(),
      text: z.boolean(), vision: z.boolean(),
    })).parse(saved.approved);
    if (selection.provider === "ollama" &&
      approved.some((m) => m.id === selection.textModel && m.text) &&
      approved.some((m) => m.id === selection.visionModel && m.vision)) {
      settings = { ...selection, apiKey: "", approved };
      return;
    }
    // Old remote/provider settings cannot activate in the local lab.
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
  if (selection.provider !== "ollama")
    throw new HttpError(503, "Настройте локальный Ollama.", "model_not_configured");
  if (selection.baseUrl !== settings.baseUrl || selection.textModel !== settings.textModel || selection.visionModel !== settings.visionModel)
    throw new HttpError(409, "Настройки моделей изменились. Создайте новый эксперимент с текущими моделями.", "model_connection_changed");
  if (!settings.approved.some((m) => m.id === selection.textModel && m.text) ||
      !settings.approved.some((m) => m.id === selection.visionModel && m.vision))
    throw new HttpError(400, "Установите Gemma 4 в Ollama и сохраните настройки моделей.", "model_unavailable");
  return { baseUrl: selection.baseUrl, apiKey: "" };
}
const inputSchema = z.object({
  provider: z.literal("ollama"), baseUrl: z.string(),
  textModel: z.string(), visionModel: z.string(),
  apiKey: z.string().trim().max(8192).optional(), clearApiKey: z.boolean().optional(),
});
function draft(raw: unknown) {
  const value = inputSchema.parse(raw);
  const selection = selectionSchema.parse(value);
  const apiKey = "";
  return { ...selection, apiKey };
}
export async function discoverModels(raw: unknown, fetcher: typeof fetch = fetch) {
  const config = draft(raw);
  try {
    const response = await fetcher(config.baseUrl + "/api/tags", {
      signal: AbortSignal.timeout(20000), redirect: "error",
    });
    if (!response.ok)
      throw new HttpError(502, `Локальный Ollama вернул HTTP ${response.status}`, "model_catalog_failed");
    const body = await response.json();
    if (!Array.isArray(body.models)) throw new Error("invalid_catalog");
    return [...new Map<string, AvailableModel>(body.models.map((m: any) => eligibleModel({id:m.name, name:m.name, details:m.details})).filter(Boolean).map((m: AvailableModel) => [m.id, m])).values()];
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, "Не удалось загрузить модели. Проверьте адрес и доступность вашего сервера.", "model_catalog_unavailable");
  }
}
export async function saveModelSettings(raw: unknown) {
  const next = draft(raw);
  let approved: AvailableModel[] = [];
  if (next.provider === "ollama") {
    if (!next.textModel || !next.visionModel)
      throw new HttpError(400, "Выберите текстовую и визуальную модели.", "model_required");
    approved = await discoverModels(next);
    if (!approved.some((m) => m.id === next.textModel && m.text) ||
        !approved.some((m) => m.id === next.visionModel && m.vision))
      throw new HttpError(400, "Установите gemma4:12b в локальном Ollama.", "model_unavailable");
  }
  settings = { ...next, approved: approved.filter((m) => m.id === next.textModel || m.id === next.visionModel) };
  await writeJson(path, settings);
  return publicModelSettings();
}
