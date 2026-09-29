import { mkdir, readFile, writeFile, rename, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { randomUUID } from "node:crypto";
import { HttpError } from "./errors.js";
import type { Project } from "./domain.js";
export const dataRoot = resolve(process.env.DATA_DIR || "data/projects");
export function projectDir(id: string) {
  if (!/^[a-f0-9-]{36}$/.test(id))
    throw new HttpError(404, "Проект не найден", "not_found");
  return join(dataRoot, id);
}
export async function writeJson(path: string, value: unknown) {
  await mkdir(resolve(path, ".."), { recursive: true, mode: 0o700 });
  const temp = path + "." + randomUUID() + ".tmp";
  await writeFile(temp, JSON.stringify(value, null, 2), { mode: 0o600 });
  await rename(temp, path);
}
// The lab has no database write queue; diagnostics use the same local store.
export const writeDiagnostic = writeJson;
export async function readJson(path: string) {
  return JSON.parse(await readFile(path, "utf8"));
}
export async function getProject(id: string): Promise<Project> {
  try {
    return await readJson(join(projectDir(id), "project.json"));
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT")
      throw new HttpError(404, "Проект не найден", "not_found");
    throw e;
  }
}
const projectWrites = new Map<string, Promise<unknown>>();
export async function saveProject(p: Project, message?: string) {
  p.updatedAt = new Date().toISOString();
  if (message) {
    p.message = message;
    p.events.push({ at: p.updatedAt, message });
    p.events = p.events.slice(-80);
  }
  const prior = projectWrites.get(p.id) || Promise.resolve();
  const write = prior
    .catch(() => {})
    .then(() => writeJson(join(projectDir(p.id), "project.json"), p));
  projectWrites.set(p.id, write);
  try {
    await write;
  } finally {
    if (projectWrites.get(p.id) === write) projectWrites.delete(p.id);
  }
  return p;
}
export async function createProject(name: string) {
  const now = new Date().toISOString();
  const p: Project = {
    id: randomUUID(),
    name,
    createdAt: now,
    updatedAt: now,
    revision: 0,
    status: "uploading",
    busy: false,
    message: "Загрузка шаблона",
    events: [],
  };
  await saveProject(p);
  return p;
}
export async function listProjects() {
  await mkdir(dataRoot, { recursive: true, mode: 0o700 });
  const values: Project[] = [];
  for (const id of await readdir(dataRoot)) {
    if (!/^[a-f0-9-]{36}$/.test(id)) continue;
    try {
      values.push(await getProject(id));
    } catch {}
  }
  return values.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
export async function recoverInterrupted() {
  for (const p of await listProjects())
    if (p.busy) {
      p.busy = false;
      p.status = "error";
      p.error = {
        code: "interrupted",
        message:
          "Сервер был перезапущен. Промежуточные файлы сохранены; повторите последний этап.",
      };
      await saveProject(p, p.error.message);
    }
}
