import "dotenv/config";
import {loadModelSettings, publicModelSettings, saveModelSettings, discoverModels, currentSelection, defaultSelection, withModelSelection} from "./model-settings.js";
import express from "express";
import {
  prepareVisualPlan,
  currentVisualPlan,
  normalizeVisualPlan,
  editVisual,
  storeVisualAsset,
} from "./visuals.js";
import multer from "multer";
import { writeFile, mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { HttpError } from "./errors.js";
import { briefSchema, type Project } from "./domain.js";
import {
  createProject,
  getProject,
  listProjects,
  projectDir,
  recoverInterrupted,
  saveProject,
  writeJson,
} from "./store.js";
import {
  analyzeSource,
  generatePlan,
  assembleAndDescribe,
  fillDeck,
  validatePlan,
  researchProject,
  adaptPlan,
} from "./pipeline.js";
import { modelInfo } from "./llm.js";
import { projectTokenUsage } from "./token-usage.js";
async function publicProject(p: Project) {
  normalizeVisualPlan(p);
  return { ...p, tokenUsage: await projectTokenUsage(projectDir(p.id)) };
}
import { nativeSandboxRevision, releaseFontSnapshot, releaseNativeRenderer } from "./sandbox.js";
import { withEngineContext, type EngineRuntime } from "./runtime-context.js";
import { ensurePlanBaseline, changedPlanSlides } from "./plan-adaptation.js";

const app = express(),
  port = Number(process.env.PORT) || 4100;
app.disable("x-powered-by");
app.use((req, res, next) => {
  const host = req.hostname,
    origin = req.get("Origin");
  if (
    !["localhost", "127.0.0.1", "::1"].includes(host) ||
    (origin && !/^http:\/\/(localhost|127\.0\.0\.1):(5174|4100)$/.test(origin))
  )
    return res.status(403).json({
      error: { message: "Доступ разрешён только из локального приложения" },
    });
  res.setHeader("X-Content-Type-Options", "nosniff");
  next();
});
app.use(express.json({ limit: "512kb" }));
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 40 * 1024 * 1024, files: 1, fields: 0 },
});
const jobs = new Map<string, AbortController>();
const writes = new Set<string>();
app.use("/api/projects/:id", (req, res, next) => {
  if (["GET", "HEAD", "OPTIONS"].includes(req.method) || req.path === "/cancel")
    return next();
  const key = req.params.id;
  if (writes.has(key))
    return res.status(409).json({
      error: { message: "Изменения уже сохраняются. Повторите запрос." },
    });
  writes.add(key);
  res.once("finish", () => writes.delete(key));
  res.once("close", () => writes.delete(key));
  next();
});
async function idle(id: string) {
  const p = await getProject(id);
  if (jobs.has(id) || p.busy)
    throw new HttpError(409, "Дождитесь завершения текущего этапа", "busy");
  normalizeVisualPlan(p);
  return p;
}
async function start(
  p: Project,
  action: string,
  run: (p: Project, s: AbortSignal) => Promise<void>,
) {
  if (jobs.size >= 3 || savingModels)
    throw new HttpError(
      409,
      "В лаборатории уже выполняется задача. Дождитесь её завершения.",
      "lab_busy",
    );
  const controller = new AbortController();
  jobs.set(p.id, controller);
  p.busy = true;
  p.lastAction = action;
  delete p.error;
  try {
    await saveProject(p, "Запускаем этап");
  } catch (e) {
    jobs.delete(p.id);
    throw e;
  }
  void (async () => {
    const runtime: EngineRuntime = {
      workspace: projectDir(p.id),
      assertActive: async () => controller.signal.throwIfAborted(),
    };
    try {
      await withEngineContext(runtime, () =>
        withModelSelection(p.modelSettings || defaultSelection(), () => run(p, controller.signal)),
      );
    } catch (e) {
      p.status = "error";
      const failure =
        e instanceof HttpError
          ? e
          : new HttpError(
              500,
              "Этап не завершён. Исходные файлы и контрольные точки сохранены.",
              "processing_failed",
            );
      p.error = controller.signal.aborted
        ? {
            code: "cancelled",
            message: "Выполнение остановлено. Завершённые шаги сохранены.",
          }
        : {
            code: failure.code,
            message: failure.message,
            details: failure.details,
          };
      await writeJson(join(projectDir(p.id), "last-error.json"), p.error);
      p.message = p.error.message;
    } finally {
      p.busy = false;
      jobs.delete(p.id);
      try {
        await releaseNativeRenderer(runtime);
        await releaseFontSnapshot(runtime);
      } finally {
        await saveProject(p, p.message);
      }
    }
  })().catch((e) =>
    console.error(
      "Failed to save job state",
      e instanceof Error ? e.name : "error",
    ),
  );
  return publicProject(p);
}
app.get("/api/settings/models", (_req,res)=>res.json(publicModelSettings()));
let savingModels=false;
app.post("/api/settings/models/discover",async(req,res)=>res.json({models:await discoverModels(req.body)}));
app.put("/api/settings/models",async(req,res)=>{
 if(jobs.size || savingModels)throw new HttpError(409,"Дождитесь завершения текущего этапа или сохранения настроек","lab_busy");
 savingModels=true;
 try{res.json(await saveModelSettings(req.body));}finally{savingModels=false;}
});
app.get("/api/health", async (_req, res) => {
  let sandbox = false;
  try {
    await nativeSandboxRevision();
    sandbox = true;
  } catch {}
  res.json({ ok: true, storage: "json-files", sandbox, models: modelInfo() });
});
app.get("/api/projects", async (_req, res) =>
  res.json(
    (await listProjects()).map((p) => ({
      id: p.id,
      name: p.name,
      status: p.status,
      updatedAt: p.updatedAt,
      busy: p.busy,
    })),
  ),
);
app.post("/api/projects", upload.single("file"), async (req, res) => {
  if (!req.file || !req.file.originalname.toLowerCase().endsWith(".pptx"))
    throw new HttpError(400, "Загрузите файл .pptx", "file_type");
  if (jobs.size >= 3 || savingModels)
    throw new HttpError(409, "Дождитесь завершения текущего этапа", "lab_busy");
  const name = Buffer.from(req.file.originalname, "latin1").toString("utf8");
  const p = await createProject(name);
  p.modelSettings=currentSelection();
  await mkdir(join(projectDir(p.id), "source"), { recursive: true });
  await writeFile(
    join(projectDir(p.id), "source/source.pptx"),
    req.file.buffer,
    { mode: 0o600 },
  );
  res.status(202).json(await start(p, "analyze", analyzeSource));
});
app.get("/api/projects/:id", async (req, res) =>
  res.json(await publicProject(await getProject(req.params.id))),
);
app.post("/api/projects/:id/plan", async (req, res) => {
  const p = await idle(String(req.params.id));
  if (!p.analysis)
    throw new HttpError(
      409,
      "Сначала дождитесь разбора шаблона",
      "no_analysis",
    );
  p.brief = { ...briefSchema.parse(req.body), webSearch: false };
  p.revision++;
  delete p.plan;
  delete p.planAdaptation;
  delete p.visuals;
  delete p.contract;
  delete p.result;
  delete p.research;
  res.status(202).json(await start(p, "plan", generatePlan));
});
app.post("/api/projects/:id/research", async (req, res) => {
  throw new HttpError(410, "Веб-поиск недоступен в открытой версии. Добавьте факты и источники в описание темы.", "search_disabled");
});
app.put("/api/projects/:id/plan", async (req, res) => {
  const p = await idle(String(req.params.id));
  if (!p.plan) throw new HttpError(409, "Сначала создайте план", "no_plan");
  if (req.body.revision !== p.revision)
    throw new HttpError(
      409,
      "План изменился. Обновите страницу.",
      "revision_conflict",
    );
  await ensurePlanBaseline(p);
  p.plan = validatePlan(p, req.body.plan);
  p.revision++;
  delete p.visuals;
  delete p.contract;
  delete p.result;
  delete p.error;
  p.status = "plan_ready";
  const assembleAfter = req.body.assembleAfter === true;
  if (
    assembleAfter ||
    (p.planAdaptation &&
      changedPlanSlides(p.planAdaptation.baseline, p.plan).length)
  ) {
    res
      .status(202)
      .json(
        await start(
          p,
          assembleAfter ? "assemble" : "adapt-plan",
          assembleAfter ? assembleAndDescribe : adaptPlan,
        ),
      );
    return;
  }
  await writeJson(join(projectDir(p.id), `plan-r${p.revision}.json`), p.plan);
  res.json(
    await publicProject(await saveProject(p, "Изменения плана сохранены")),
  );
});
app.post("/api/projects/:id/assemble", async (req, res) => {
  const p = await idle(String(req.params.id));
  if (!p.plan) throw new HttpError(409, "Сначала создайте план", "no_plan");
  if (req.body.revision !== p.revision)
    throw new HttpError(
      409,
      "План изменился. Обновите страницу.",
      "revision_conflict",
    );
  res.status(202).json(await start(p, "assemble", assembleAndDescribe));
});
app.post("/api/projects/:id/fill", async (req, res) => {
  const p = await idle(String(req.params.id));
  if (!p.contract || p.contract.revision !== p.revision)
    throw new HttpError(
      409,
      "Сначала соберите и проанализируйте макет",
      "no_contract",
    );
  res.status(202).json(await start(p, "fill", fillDeck));
});
app.post("/api/projects/:id/visuals/plan", async (req, res) => {
  const p = await idle(String(req.params.id));
  if (!p.contract)
    throw new HttpError(409, "Сначала соберите макеты", "no_contract");
  res.status(202).json(
    await start(p, "visual-plan", async (p, s) => {
      await prepareVisualPlan(p, s);
      p.status = p.result ? "complete" : "template_ready";
    }),
  );
});
app.put("/api/projects/:id/visuals", async (req, res) => {
  const p = await idle(String(req.params.id));
  editVisual(p, req.body);
  res.json(
    await publicProject(
      await saveProject(
        p,
        "Настройки изображения сохранены. Соберите результат, чтобы применить замену.",
      ),
    ),
  );
});
const imageUpload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 24 * 1024 * 1024, files: 1, fields: 0 },
});
app.post(
  "/api/projects/:id/visuals/:revision/:slide/:slot/upload",
  imageUpload.single("file"),
  async (req, res) => {
    const p = await idle(String(req.params.id)),
      plan = currentVisualPlan(p);
    if (!plan || String(p.revision) !== req.params.revision)
      throw new HttpError(
        409,
        "Макет изменился. Обновите страницу.",
        "revision_conflict",
      );
    const c = plan.choices.find(
      (c) =>
        String(c.slideIndex) === req.params.slide &&
        String(c.slotIndex) === req.params.slot,
    );
    if (!c || c.slot.protected || c.preserveReason)
      throw new HttpError(
        400,
        "Это место недоступно для замены",
        "visual_protected",
      );
    if (!req.file)
      throw new HttpError(
        400,
        "Выберите PNG, JPEG или WebP",
        "visual_upload_required",
      );
    const asset = await storeVisualAsset(
      projectDir(p.id),
      req.file.buffer,
      false,
      c.slot.requiresOpaque,
    );
    Object.assign(c, {
      mode: "upload",
      asset,
      status: "ready",
      applied: false,
    });
    delete c.message;
    delete c.generationHash;
    delete p.result;
    p.status = "template_ready";
    res.json(
      await publicProject(
        await saveProject(
          p,
          "Изображение загружено. Соберите результат, чтобы применить замену.",
        ),
      ),
    );
  },
);
app.post("/api/projects/:id/retry", async (req, res) => {
  const p = await idle(String(req.params.id));
  const actions: Record<string, (p: Project, s: AbortSignal) => Promise<void>> =
    {
      analyze: analyzeSource,
      plan: generatePlan,
      "adapt-plan": adaptPlan,
      assemble: assembleAndDescribe,
      fill: fillDeck,
      research: researchProject,
      "visual-plan": async (p, s) => {
        await prepareVisualPlan(p, s);
        p.status = p.result ? "complete" : "template_ready";
      },
    };
  const fn = actions[p.lastAction || ""];
  if (!fn) throw new HttpError(409, "Нет этапа для повтора", "no_retry");
  res.status(202).json(await start(p, p.lastAction!, fn));
});
app.post("/api/projects/:id/cancel", async (req, res) => {
  jobs.get(req.params.id)?.abort();
  res.json({ ok: true });
});
// Only known artifacts. Never publish the data directory, .env, or model requests.
app.get("/api/projects/:id/files/:kind/:name", async (req, res) => {
  const p = await getProject(req.params.id),
    { kind, name } = req.params,
    root = projectDir(p.id);
  let path: string | undefined;
  if (kind === "visual" && /^[a-f0-9]{64}\.png$/.test(name))
    path = join(root, "visual-assets", name);
  if (
    kind === "source" &&
    /^(source\.pptx|analysis\.json|parser\.json|frames\.json)$/.test(name)
  )
    path = join(root, "source", name);
  if (kind === "source" && /^slide-\d{1,3}\.png$/.test(name))
    path = join(root, "source/previews", name);
  if (
    kind === "assembled" &&
    /^(source\.pptx|presentation\.pptx|analysis\.json|parser\.json|frames\.json)$/.test(
      name,
    )
  )
    path = join(root, `assembled-r${p.revision}`, name);
  if (kind === "assembled" && /^slide-\d{1,3}\.png$/.test(name))
    path = join(root, `assembled-r${p.revision}/previews`, name);
  if (
    kind === "output" &&
    /^(presentation\.(pptx|pdf)|slide-\d{1,3}\.png|quality-warnings\.json|render-quality\.json|package-cleanup\.json|field-changes\.json)$/.test(
      name,
    )
  )
    path = join(root, `output-r${p.revision}`, name);
  if (kind === "json" && ["template", "filled", "plan"].includes(name))
    path = join(root, `${name}-r${p.revision}.json`);
  if (kind === "json" && name === "error") path = join(root, "last-error.json");
  if (kind === "json" && name === "research" && p.research)
    path = join(root, "research.json");
  if (
    kind === "output" &&
    /^(presentation\.(pptx|pdf)|slide-\d{1,3}\.png)$/.test(name) &&
    (p.busy || p.status !== "complete" || !p.result)
  )
    throw new HttpError(
      409,
      "Результат ещё проходит автоматическую проверку и исправление",
      "output_not_validated",
    );
  if (kind === "output" && /^(presentation\.pdf|slide-\d{1,3}\.png)$/.test(name) && p.result?.pdfAvailable === false)
    throw new HttpError(404,"PDF и предпросмотр недоступны; скачайте PPTX","preview_unavailable");
  if (kind === "json" && name === "repair")
    path = join(root, `output-r${p.revision}`, "repair-state.json");
  if (!path) throw new HttpError(404, "Файл не найден", "not_found");
  res.setHeader("Cache-Control", "no-store");
  res.sendFile(path, (error) => {
    if (error && !res.headersSent)
      res.status(404).json({ error: { message: "Файл ещё не создан" } });
  });
});
app.use(express.static(resolve("../frontend/dist")));
app.use(
  (
    error: any,
    _req: express.Request,
    res: express.Response,
    _next: express.NextFunction,
  ) => {
    const status =
      error instanceof HttpError
        ? error.status
        : error instanceof z.ZodError
          ? 400
          : error instanceof multer.MulterError
            ? 413
            : 500;
    res.status(status).json({
      error: {
        code: error.code || "request_failed",
        message:
          error instanceof z.ZodError
            ? "Проверьте заполнение полей"
            : error instanceof multer.MulterError
              ? "Файл слишком большой. Максимум 40 МБ"
              : error instanceof HttpError
                ? error.message
                : "Не удалось выполнить запрос",
        details: error instanceof HttpError ? error.details : undefined,
      },
    });
  },
);
// Acquire the listening port before touching saved jobs. A second launcher must
// never mark the first process's active generation as interrupted.
await loadModelSettings();
await new Promise<void>((resolve, reject) => {
  app.listen(port, "127.0.0.1", (error?: Error) =>
    error ? reject(error) : resolve(),
  );
});
await recoverInterrupted();
console.log(
  `Nerpa Template Lab: http://localhost:${port} · JSON storage · no database`,
);
