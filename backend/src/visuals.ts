import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile, rename } from "node:fs/promises";
import { join } from "node:path";
import sharp, { type Metadata } from "sharp";
import { createPool } from "./async-pool.js";
import { parallelSlides } from "./parallel-slides.js";
import { z } from "zod";
import type { Project, Layout } from "./domain.js";
import { HttpError } from "./errors.js";
import { llmJson } from "./llm.js";
import { projectDir, readJson, writeJson, saveProject } from "./store.js";
import { runNativeSandbox } from "./sandbox.js";
import {
  visualAnalysisPrompt,
  visualPreserveReason,
  modelVisualSlots,
  validateVisualDescriptions,
  reconcileVisualDescriptions,
  visualKinds,
  type VisualPlan,
  type VisualChoice,
  type VisualSlot,
} from "./visual-types.js";
const hash = (v: unknown) =>
  createHash("sha256").update(JSON.stringify(v)).digest("hex");
const assetName = /^[a-f0-9]{64}\.png$/;
export function currentVisualPlan(p: Project): VisualPlan | undefined {
  return p.visuals?.revision === p.revision &&
    p.visuals.assembledSha256 === p.contract?.assembledSha256
    ? p.visuals
    : undefined;
}
export function normalizeVisualPlan(p: Project) {
  const plan = currentVisualPlan(p);
  if (!plan) return false;
  let changed = false;
  for (const choice of plan.choices) {
    if (choice.mode === "generate") {
      choice.mode = "keep";
      choice.applied = false;
      changed = true;
    }
    const reason = visualPreserveReason(choice, p.contract.dimensions);
    if (!reason) {
      if (choice.preserveReason) {
        delete choice.preserveReason;
        changed = true;
      }
      continue;
    }
    if (
      choice.mode !== "keep" ||
      choice.preserveReason !== reason ||
      choice.applied
    )
      changed = true;
    choice.mode = "keep";
    choice.preserveReason = reason;
    choice.applied = false;
    // Keep paid assets and receipts for audit; never attach them to preserved marks.
  }
  return changed;
}
export function nearestVisualFields(
  slot: VisualSlot,
  layout: Layout,
  fields?: Record<string, string>,
) {
  return layout.slots
    .filter(
      (s) =>
        (!fields || fields[s.key]?.trim()) &&
        !["page_number", "brand", "decoration"].includes(s.role),
    )
    .map((s) => ({
      key: s.key,
      gap: Math.hypot(
        Math.max(0, slot.box.x - s.x - s.w, s.x - slot.box.x - slot.box.w),
        Math.max(0, slot.box.y - s.y - s.h, s.y - slot.box.y - slot.box.h),
      ),
      center: Math.hypot(
        s.x + s.w / 2 - slot.box.x - slot.box.w / 2,
        s.y + s.h / 2 - slot.box.y - slot.box.h / 2,
      ),
    }))
    .sort((a, b) => a.gap - b.gap || a.center - b.center)
    .slice(0, 3)
    .map((s) => s.key);
}
export async function visualMetadata(p: Project) {
  const folder = join(projectDir(p.id), `assembled-r${p.revision}`);
  const result: any = await runNativeSandbox(
    { action: "layout_metadata", folder },
    240000,
  );
  if (result.error)
    throw new HttpError(
      422,
      "Не удалось прочитать места изображений в PPTX",
      "visual_metadata",
    );
  return result as Record<string, { visualSlots: VisualSlot[] }>;
}
export async function prepareVisualPlan(
  p: Project,
  signal: AbortSignal,
  measuredMetadata?: Record<string, { visualSlots: VisualSlot[] }>,
) {
  const previousPlan = currentVisualPlan(p);
  if (previousPlan?.detectionVersion === 2) {
    if (normalizeVisualPlan(p)) await saveProject(p);
    return;
  }
  if (!p.contract)
    throw new HttpError(409, "Сначала соберите макеты", "no_contract");
  const folder = projectDir(p.id),
    assembled = join(folder, `assembled-r${p.revision}`);
  // Assembly has just measured these same immutable native slides. Reuse that
  // result; do not run all character-budget probes a second time for pictures.
  const metadata = measuredMetadata || (await visualMetadata(p));
  const plan: VisualPlan = {
    version: 1,
    detectionVersion: 2,
    revision: p.revision,
    assembledSha256: p.contract.assembledSha256,
    choices: [],
  };
  await parallelSlides<any>(
    p.contract.slides,
    signal,
    async (slide, i, signal) => {
      signal.throwIfAborted();
      const slots = metadata[slide.assembledLayoutId]?.visualSlots || [];
      const previousSlots: VisualSlot[] = slide.native.visualSlots || [];
      const reusable = slots.flatMap((slot, slotIndex) => {
        const choice = previousPlan?.choices.find(
          (c) => c.slideIndex === i && c.shapeId === slot.shapeId,
        );
        const oldIndex = previousSlots.findIndex(
          (s) => s.shapeId === slot.shapeId,
        );
        const old =
          choice ||
          slide.semantics.images?.find((d: any) => d.slotIndex === oldIndex);
        return old ? [{ ...old, slotIndex }] : [];
      });
      if (!slots.length) return;
      const fingerprint = hash({
        v: 2,
        sha: plan.assembledSha256,
        slots,
        plan: p.plan?.slides[i],
      });
      const path = join(assembled, `visual-plan-${i}.json`);
      let saved: any;
      try {
        saved = await readJson(path);
      } catch {}
      let descriptions;
      try {
        descriptions = reconcileVisualDescriptions(
          reusable,
          slots,
          slide.native.slots.map((s: any) => s.key),
        );
      } catch {}
      if (!descriptions && saved?.fingerprint === fingerprint) {
        try {
          descriptions = reconcileVisualDescriptions(
            saved.value,
            slots,
            slide.native.slots.map((s: any) => s.key),
          );
        } catch {
          // A corrupt/stale cache is not a reason to fail the whole deck.
          // The model below can describe these native slots again.
        }
      }
      if (!descriptions && !modelVisualSlots(slots).length)
        descriptions = validateVisualDescriptions([], slots, []);
      if (!descriptions) {
        await saveProject(
          p,
          `Определяем назначение изображений: слайд ${i + 1}`,
        );
        descriptions = await llmJson({
          stage: `visual-plan-${i + 1}`,
          folder,
          signal,
          prompt:
            "Опиши только изображения, не переписывай текст. Ответ {images:[...]}. " +
            visualAnalysisPrompt,
          payload: {
            approved: p.plan?.slides[i],
            visualSlots: modelVisualSlots(slots).filter(
              (s) => !reusable.some((r) => r.slotIndex === s.slotIndex),
            ),
            fields: slide.native.slots.map((s: any) => ({
              key: s.key,
              x: s.x,
              y: s.y,
              w: s.w,
              h: s.h,
              text: s.text.slice(0, 160),
            })),
            dimensions: p.contract.dimensions,
          },
          images: [join(assembled, "previews", `slide-${i}.png`)],
          validate: (raw) =>
            validateVisualDescriptions(
              [
                ...reusable,
                ...((raw as any)?.images || []).filter(
                  (d: any) =>
                    !reusable.some((r) => r.slotIndex === d.slotIndex),
                ),
              ],
              slots,
              slide.native.slots.map((s: any) => s.key),
            ),
        });
      }
      await writeJson(path, { fingerprint, value: descriptions });
      slide.native.visualSlots = slots;
      slide.semantics.images = descriptions;
      for (const d of descriptions) {
        const slot = slots[d.slotIndex];
        const previous = previousPlan?.choices.find(
          (c) => c.slideIndex === i && c.shapeId === slot.shapeId,
        );
        plan.choices.push({
          ...d,
          slideIndex: i,
          shapeId: slot.shapeId,
          slot,
          mode:
            ["content", "photo_underlay"].includes(d.role) && !slot.protected
              ? "keep"
              : "keep",
          instruction: "",
          quality: "medium",
          nonce: 0,
          ...(previous
            ? {
                ...previous,
                slotIndex: d.slotIndex,
                slot,
                shapeId: slot.shapeId,
              }
            : {}),
        });
      }
    },
  );
  plan.choices.sort(
    (a, b) => a.slideIndex - b.slideIndex || a.slotIndex - b.slotIndex,
  );
  p.visuals = plan;
  normalizeVisualPlan(p);
  await writeJson(join(folder, `template-r${p.revision}.json`), p.contract);
  await saveProject(
    p,
    "Крупные изображения выбраны автоматически. Иконки, логотипы и декор сохраняются.",
  );
}
export const visualEditSchema = z.object({
  revision: z.number().int(),
  slideIndex: z.number().int().min(0),
  slotIndex: z.number().int().min(0),
  mode: z.enum(["keep", "generate", "upload"]),
  kind: z.enum(visualKinds),
  background: z.enum(["transparent", "opaque"]),
  instruction: z.string().trim().max(1000),
  quality: z.enum(["low", "medium", "high"]),
  regenerate: z.boolean().optional(),
});
export function editVisual(p: Project, raw: unknown) {
  normalizeVisualPlan(p);
  const edit = visualEditSchema.parse(raw),
    plan = currentVisualPlan(p);
  if (!plan || edit.revision !== p.revision)
    throw new HttpError(
      409,
      "Макет изменился. Обновите страницу.",
      "revision_conflict",
    );
  const choice = plan.choices.find(
    (c) => c.slideIndex === edit.slideIndex && c.slotIndex === edit.slotIndex,
  );
  if (!choice)
    throw new HttpError(
      404,
      "Место изображения не найдено",
      "visual_not_found",
    );
  if (edit.mode === "generate")
    throw new HttpError(410, "Генерация новых изображений отключена в открытой версии. Загрузите свой файл.", "image_generation_disabled");
  if ((choice.slot.protected || choice.preserveReason) && edit.mode !== "keep")
    throw new HttpError(
      400,
      "Логотип или служебный элемент защищён от замены",
      "visual_protected",
    );
  if (edit.mode === "upload" && !choice.asset)
    throw new HttpError(
      400,
      "Сначала загрузите изображение",
      "visual_upload_required",
    );
  const next = {
    mode: edit.mode,
    kind: edit.kind,
    background: choice.slot.requiresOpaque
      ? ("opaque" as const)
      : edit.background,
    instruction: edit.instruction,
    quality: edit.quality,
  };
  const changed =
    Object.entries(next).some(([k, v]) => (choice as any)[k] !== v) ||
    edit.regenerate;
  if (changed) {
    Object.assign(choice, next);
    choice.applied = false;
    delete choice.message;
    if (edit.mode !== "upload") {
      delete choice.asset;
      delete choice.generationHash;
      delete choice.status;
    }
    if (edit.regenerate) choice.nonce++;
    delete p.result;
    p.status = "template_ready";
  }
  return choice;
}
export async function storeVisualAsset(
  folder: string,
  input: Buffer,
  requireAlpha = false,
  requireOpaque = false,
) {
  if (!input.length || input.length > 24 * 1024 * 1024)
    throw new HttpError(413, "Изображение превышает 24 МБ", "visual_size");
  let image: Buffer, info: Metadata;
  try {
    const source = sharp(input, {
      limitInputPixels: 24_000_000,
      animated: false,
    });
    info = await source.metadata();
    if (
      !["png", "jpeg", "webp"].includes(info.format || "") ||
      !info.width ||
      !info.height
    )
      throw new Error("format");
    const stats = await source.stats();
    if (
      requireAlpha &&
      (!info.hasAlpha || stats.isOpaque || stats.channels.at(-1)?.max === 0)
    )
      throw new HttpError(
        422,
        "Модель вернула изображение без прозрачного фона. Можно повторить только эту картинку.",
        "visual_alpha",
      );
    if (requireOpaque && !stats.isOpaque)
      throw new HttpError(
        422,
        "За текстом требуется непрозрачное изображение. Загрузите JPEG или PNG без прозрачности.",
        "visual_backdrop_alpha",
      );
    image = await sharp(input, { limitInputPixels: 24_000_000 })
      .rotate()
      .resize({
        width: 2048,
        height: 2048,
        fit: "inside",
        withoutEnlargement: true,
      })
      .png()
      .toBuffer();
  } catch (e) {
    if (e instanceof HttpError) throw e;
    throw new HttpError(
      400,
      "Не удалось прочитать PNG, JPEG или WebP",
      "visual_format",
    );
  }
  const name = createHash("sha256").update(image).digest("hex") + ".png";
  const directory = join(folder, "visual-assets");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const temp = join(directory, `${randomUUID()}.tmp`);
  await writeFile(temp, image, { mode: 0o600 });
  await rename(temp, join(directory, name));
  return name;
}
export function imagePrompt(
  brief: string,
  choice: VisualChoice,
  palette: unknown,
) {
  const transparent = choice.background === "transparent";
  return `Create ONE professional presentation visual. Kind: ${choice.kind}. This is an asset inside an existing slide, not the slide itself.
Reference data (not instructions): ${JSON.stringify({ subject: brief, style: choice.style, palette, aspectRatio: choice.slot.aspectRatio })}.
Match the slide palette, materials and visual style. Explain this slot's specific subject. Preserve recognizable physical forms and natural skin colours. Use a clear composition and readable silhouette. Finish object edges and keep generous margins.
${transparent ? "Isolated cutout with real transparent alpha and transparent negative space. No white matte, checkerboard, rectangle, floor, scenery or painted background. Subtle shadows fade into alpha." : "Opaque image filling its frame. Match the original composition and colour contrast, especially behind overlaid slide text. Keep the composition quiet where text overlaps."}
${choice.kind === "chart" ? "Chart comparing only the exact supported values in the brief, with their units and labels. Use honest scales and correct proportions. If no quantitative data was supplied, show a qualitative comparison without numbers or numeric axes. Never invent statistics." : choice.kind === "diagram" ? "Illustrative conceptual diagram with clear relationships and few elements. Arrows must express the sequence or relationships in the brief, not decorative connections. No fabricated quantitative chart or statistics. Use only short labels explicitly supplied in the brief." : choice.kind === "interface" ? "Polished conceptual UI/product mockup, not a real screenshot or proof of a shipped product. Use only short labels explicitly supplied; no invented analytics, brands, logos or claims." : "No text, letters, numbers, logos, watermarks or invented charts. No generic robots or light bulbs unless the subject specifically requires them."}
For diagrams and interfaces, all visible UI labels must be in Russian unless the user explicitly requests another language. Short generic navigation labels are allowed; do not invent quantitative metrics. Do not duplicate the slide title or draw its decorations. Never reproduce template placeholder instructions such as "insert photo" or "Вставить фото". No extraneous objects.`;
}
type GenerateInput = {
  folder: string;
  prompt: string;
  choice: VisualChoice;
  dimensions: { width: number; height: number };
  signal: AbortSignal;
  fetcher?: typeof fetch;
};
export function generateVisualAsset(_input: GenerateInput): Promise<{asset: string; identity: string}> {
  return Promise.reject(new HttpError(410,
    "Генерация новых изображений отключена в открытой версии. Загрузите свой файл.",
    "image_generation_disabled"));
}
/** A ready slide starts its visual work while later slides are still filling.
 * Bounded pools and a single-flight cache avoid duplicate paid calls. */
export async function createVisualSession(
  p: Project,
  parentSignal: AbortSignal,
  deps: { generate?: typeof generateVisualAsset; brief?: typeof llmJson } = {},
) {
  await prepareVisualPlan(p, parentSignal);
  const controller = new AbortController();
  const signal = AbortSignal.any([parentSignal, controller.signal]);
  const imagePool = createPool(3, signal),
    briefPool = createPool(2, signal);
  const folder = projectDir(p.id),
    plan = currentVisualPlan(p)!;
  const tasks = new Map<number, Promise<void>>();
  const work = new Map<number, { slide: any; task: Promise<void> }>();
  const images: Record<string, string> = {};
  async function processSlide(i: number, slide: any, planned = false) {
    signal.throwIfAborted();
    const choices = plan.choices.filter(
      (c) =>
        c.slideIndex === i &&
        c.mode !== "keep" &&
        !c.slot.protected &&
        !c.preserveReason,
    );
    // A new wording pass must not buy the same approved image again. Editing
    // its settings or revision explicitly invalidates the stored asset.
    const generated = choices.filter(
      (c) =>
        c.mode === "generate" &&
        !(c.status === "ready" && c.asset && c.generationHash),
    );
    let briefs: Record<string, string> = {};
    if (generated.length) {
      const layout = p.contract.slides[i].native as Layout;
      const payload = {
        title: slide.title,
        ...(planned
          ? { approved: p.plan!.slides[i], contentBasis: "approved_plan" }
          : {}),
        style: p.contract.styleProfile,
        slots: generated.map((c) => ({
          slotIndex: c.slotIndex,
          kind: c.kind,
          background: c.background,
          instruction: c.instruction,
          original: c.description,
          style: c.style,
          nearbyText: (c.fieldKeys.filter((key) =>
            slide.native.fields[key]?.trim(),
          ).length
            ? c.fieldKeys
                .filter((key) => slide.native.fields[key]?.trim())
                .slice(0, 5)
            : nearestVisualFields(c.slot, layout, slide.native.fields)
          ).map((key) => ({ key, text: slide.native.fields[key] })),
        })),
        otherSubjects: plan.choices
          .filter((c) => c.slideIndex !== i && c.mode === "generate")
          .map((c) => c.description.slice(0, 100))
          .slice(0, 12),
      };
      // Each slot has its own semantic cache: changing another picture never
      // invalidates this paid image. Previous subjects guide only fresh briefs.
      const pending: typeof payload.slots = [];
      const paths = new Map<number, string>();
      for (const slot of payload.slots) {
        const key = hash({
          v: planned ? 3 : 2,
          title: payload.title,
          ...(planned ? { approved: p.plan!.slides[i] } : {}),
          style: payload.style,
          slot,
        });
        const file = join(folder, "visual-briefs", `${key}.json`);
        paths.set(slot.slotIndex, file);
        try {
          const saved = await readJson(file);
          if (typeof saved.brief !== "string" || saved.brief.length < 10)
            throw new Error("invalid_brief");
          briefs[String(slot.slotIndex)] = saved.brief;
        } catch {
          pending.push(slot);
        }
      }
      if (pending.length) {
        const fresh = await briefPool(() =>
          (deps.brief || llmJson)({
            stage: `visual-brief-${i + 1}`,
            folder,
            signal,
            prompt:
              "Составь короткое точное английское задание на каждую картинку. Если contentBasis=approved_plan, опирайся на approved и instruction: финальные тексты ещё пишутся, старый образец не является фактами новой темы. Иначе используй ИТОГОВОЙ nearbyText. Сохрани стиль, но замени старую тему. Разные места — разные предметы/сцены. Только подтверждённый видимый смысл; не выдумывай числовые данные, реальные продукты или логотипы. Схема — логичные связи или последовательность из тезисов, interface — явно концепт. Для kind=chart передай только точные подтверждённые величины, единицы и подписи из approved/nearbyText; без них опиши качественное сравнение без числовой шкалы. Для остальных kind не рисуй фактические значения, таблицы и точные графики. Соблюдай заданные kind/background, пользовательское instruction, стиль и палитру слайда. Не переписывай тексты слайда. Ответ {briefs:{slotIndex:brief}}, 40–100 слов на место.",
            payload: {
              ...payload,
              slots: pending,
              existingSubjects: Object.values(briefs),
            },
            validate: (raw) => {
              const v = z
                .object({
                  briefs: z.record(
                    z.string(),
                    z.string().trim().min(10).max(1400),
                  ),
                })
                .parse(raw).briefs;
              const keys = pending.map((c) => String(c.slotIndex));
              if (
                Object.keys(v).length !== keys.length ||
                keys.some((k) => !v[k])
              )
                throw new Error("visual_brief_mapping");
              return v;
            },
          }),
        );
        for (const slot of pending) {
          const brief = fresh[String(slot.slotIndex)];
          briefs[String(slot.slotIndex)] = brief;
          await writeJson(paths.get(slot.slotIndex)!, { brief });
        }
      }
    }
    slide.images = [];
    slide.visualSlotsVersion = 1;
    const settledImages = await Promise.allSettled(
      choices.map((choice) =>
        imagePool(async () => {
          signal.throwIfAborted();
          try {
            if (generated.includes(choice)) {
              const prompt = imagePrompt(
                briefs[String(choice.slotIndex)],
                choice,
                p.contract.styleProfile,
              );
              choice.status = "generating";
              await saveProject(
                p,
                `Изображение для слайда ${i + 1}, место ${choice.slotIndex + 1}`,
              );
              signal.throwIfAborted();
              const generated = await (deps.generate || generateVisualAsset)({
                folder,
                prompt,
                choice,
                dimensions: p.contract.dimensions,
                signal,
              });
              choice.asset = generated.asset;
              choice.generationHash = generated.identity;
            }
            if (!choice.asset || !assetName.test(choice.asset))
              throw new HttpError(
                422,
                "Выберите файл для этого места",
                "visual_missing",
              );
            await readFile(join(folder, "visual-assets", choice.asset));
            images[choice.asset] = join(folder, "visual-assets", choice.asset);
            slide.images.push({
              slotIndex: choice.slotIndex,
              shapeId: choice.shapeId,
              image: choice.asset,
              background: choice.background,
            });
            choice.status = "ready";
            // The exporter confirms application after its PPTX/PDF checks.
            choice.applied = false;
            delete choice.message;
          } catch (e) {
            signal.throwIfAborted();
            choice.applied = false;
            choice.status =
              e instanceof HttpError && e.code !== "visual_previous_attempt"
                ? "failed"
                : "uncertain";
            choice.message =
              e instanceof HttpError
                ? e.message
                : "Ответ генератора не получен. Сохранён оригинал; повторите только эту картинку.";
          }
          await saveProject(p);
        }),
      ),
    );
    const failedImage = settledImages.find((r) => r.status === "rejected");
    if (failedImage?.status === "rejected") throw failedImage.reason;
    slide.images.sort((a: any, b: any) => a.slotIndex - b.slotIndex);
  }
  function start(i: number, slide: any, planned = false) {
    const existing = work.get(i);
    if (existing) return existing;
    const snapshot = { ...slide, native: { ...slide.native }, images: [] };
    const task = processSlide(i, snapshot, planned).catch(async (error) => {
      signal.throwIfAborted();
      // Image briefing is optional work. A provider failure must not throw
      // away completed text or restart all paid image requests.
      for (const choice of plan.choices.filter(
        (c) => c.slideIndex === i && c.mode !== "keep" && !c.preserveReason,
      )) {
        choice.status = "failed";
        choice.applied = false;
        choice.message =
          "Не удалось подготовить изображение. Сохранён исходный рисунок.";
      }
      await saveProject(p);
    });
    void task.catch(() => {});
    const item = { slide: snapshot, task };
    work.set(i, item);
    return item;
  }
  return {
    prewarm() {
      // Start only after the user's fill action, against the approved plan.
      // Data/process diagrams still wait for final neighbouring text.
      for (const [i, approved] of (p.plan?.slides || []).entries()) {
        const choices = plan.choices.filter(
          (c) =>
            c.slideIndex === i &&
            c.mode !== "keep" &&
            !c.preserveReason &&
            !c.slot.protected,
        );
        if (!choices.length || choices.some((c) => ["diagram", "chart"].includes(c.kind)))
          continue;
        start(i, { title: approved.title, native: { fields: {} } }, true);
      }
    },
    enqueue(i: number, slide: any) {
      if (tasks.has(i)) throw new Error("visual_slide_scheduled_twice");
      const job = start(i, slide);
      const task = job.task.then(() => {
        signal.throwIfAborted();
        slide.images = (job.slide.images || []).filter((image: any) => {
          const choice = plan.choices.find(
            (c) => c.slideIndex === i && c.shapeId === image.shapeId,
          );
          if (!choice || !canAttachVisual(slide, choice)) {
            if (choice) {
              choice.applied = false;
              choice.message =
                "Место изображения изменилось при восстановлении. Сохранён оригинал.";
            }
            return false;
          }
          return true;
        });
        slide.visualSlotsVersion = 1;
      });
      void task.catch(() => {});
      tasks.set(i, task);
    },
    async finish() {
      const settled = await Promise.allSettled([
        ...tasks.values(),
        ...[...work.values()].map((w) => w.task),
      ]);
      const failure = settled.find((r) => r.status === "rejected");
      if (failure?.status === "rejected") throw failure.reason;
      return images;
    },
    async stop() {
      controller.abort();
      await Promise.allSettled([
        ...tasks.values(),
        ...[...work.values()].map((w) => w.task),
      ]);
    },
  };
}
export function canAttachVisual(slide: any, choice: VisualChoice) {
  if (
    slide.native?.preserveSource ||
    slide.native?.composition ||
    choice.slot.protected ||
    choice.preserveReason
  )
    return false;
  const scene = slide.native?.rebuild;
  if (!scene) return true;
  // Native placeholders are unchanged by text recomposition and are validated
  // again against the source XML. Real pictures must retain their exact frame.
  if (choice.slot.kind === "placeholder") return true;
  const picture = scene.pictures?.find(
    (p: any) => p.shapeId === choice.shapeId,
  );
  return (
    !!picture &&
    (["x", "y", "w", "h"] as const).every(
      (k) =>
        Number.isFinite(picture[k]) &&
        Math.abs(picture[k] - choice.slot.box[k]) < 0.2,
    )
  );
}
export async function applyVisuals(
  p: Project,
  slides: any[],
  signal: AbortSignal,
) {
  const session = await createVisualSession(p, signal);
  try {
    slides.forEach((slide, i) => session.enqueue(i, slide));
    return await session.finish();
  } finally {
    await session.stop();
  }
}
