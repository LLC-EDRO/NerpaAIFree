import { parallelSlides } from "./parallel-slides.js";
import { HttpError } from "./errors.js";
import { join } from "node:path";
import { readdir } from "node:fs/promises";
import { z } from "zod";
import { numbers, type Outline, type Project, type Layout } from "./domain.js";
import { tableColumnEvidence } from './tabular-evidence.js';
import { digest } from "./repair.js";
import { llmJson } from "./llm.js";
import { projectDir, readJson, writeJson, saveProject } from "./store.js";

export function changedPlanSlides(before: Outline, after: Outline) {
  return after.slides.flatMap((slide, index) => {
    const oldIndex = slide.id
      ? before.slides.findIndex((s) => s.id === slide.id)
      : index;
    const old = before.slides[oldIndex];
    return !old ||
      oldIndex !== index ||
      old.sourceLayoutId !== slide.sourceLayoutId ||
      old.title !== slide.title ||
      old.brief !== slide.brief
      ? [index]
      : [];
  });
}
export async function ensurePlanBaseline(p: Project) {
  if (p.planAdaptation || !p.plan) return;
  // Recover user edits saved by the version that did not adapt plans at all.
  const revisions = (await readdir(projectDir(p.id)))
    .map((name) => /^plan-r(\d+)\.json$/.exec(name))
    .filter((m) => m && Number(m[1]) < p.revision)
    .map((m) => Number(m![1]))
    .sort((a, b) => b - a);
  const baseline = revisions.length
    ? await readJson(join(projectDir(p.id), `plan-r${revisions[0]}.json`))
    : p.plan;
  p.planAdaptation = { version: 1, baseline: structuredClone(baseline) };
}
const patchSchema = z.object({
  slides: z.array(
    z.object({
      index: z.number().int().min(0),
      sourceLayoutId: z.string(),
      title: z.string().trim().min(1).max(200),
      brief: z.string().trim().min(1).max(2000),
    }),
  ),
});
export function adaptationPatch(
  raw: unknown,
  plan: Outline,
  targets: number[],
  allowedNumbers: Set<string>,
  fieldKeys: Set<string> = new Set(),
  allowPartial = false,
) {
  const result = patchSchema.parse(raw);
  if (
    (!allowPartial && result.slides.length !== targets.length) ||
    new Set(result.slides.map((s) => s.index)).size !== result.slides.length ||
    result.slides.some((s) => !targets.includes(s.index))
  )
    throw new Error(
      `Верни слайды с индексами ${targets.join(', ')} ровно один раз. Получены: ${result.slides.map(s=>s.index).join(', ')}`,
    );
  const rejected: {index:number; reason:string}[] = [];
  const accepted = result.slides.filter(slide => {
    if (slide.sourceLayoutId !== plan.slides[slide.index].sourceLayoutId)
      throw new Error("Выбранный пользователем макет менять нельзя");
    if (
      numbers(
        (slide.title + "\n" + slide.brief).replace(
          /\b[A-Za-z_][A-Za-z0-9_]*\b/g,
          (key) => (fieldKeys.has(key) ? "" : key),
        ),
      ).some((n) => !allowedNumbers.has(n))
    ) {
      const reason = "Числа не сопоставлены с материалами. Проверь числовую запись и подтверждение, сохраняя смысл.";
      if (!allowPartial) throw new Error(reason);
      rejected.push({index:slide.index,reason});
      return false;
    }
    return true;
  });
  return {slides:accepted, rejected};
}
export function mergeAdaptation(
  plan: Outline,
  patches: ReturnType<typeof adaptationPatch>["slides"],
) {
  const result = structuredClone(plan);
  for (const patch of patches) {
    const slide = result.slides[patch.index];
    // Identity, selection and order belong to the user, never to the model.
    result.slides[patch.index] = {
      ...slide,
      title: patch.title,
      brief: patch.brief,
    };
  }
  return result;
}

export async function adaptSelectedPlan(
  p: Project,
  signal: AbortSignal,
  metadata: () => Promise<Record<string, any>>,
  model: typeof llmJson = llmJson,
) {
  await ensurePlanBaseline(p);
  if (!p.plan || !p.planAdaptation) return false;
  const input = structuredClone(p.plan);
  const targets = changedPlanSlides(p.planAdaptation.baseline, input);
  if (!targets.length) return false;
  p.status = "adapting_plan";
  p.progress = { done: 0, total: targets.length };
  await saveProject(
    p,
    `AI адаптирует план под выбранные макеты: ${targets.length} слайдов`,
  );
  const layouts = await metadata();
  const folder = projectDir(p.id);
  const evidence = p.brief?.webSearch ? p.research?.evidence || [] : [];
  const allowed = new Set(
      [
        p.brief!.topic,
        p.brief!.sourceText,
        ...input.slides.flatMap((s) => [s.title, s.brief]),
        ...evidence.map((f) => f.text),
      ].flatMap(text => [numbers(text || ''), ...tableColumnEvidence(text || '').map(numbers)]).flat(),
  );
  const patches: ReturnType<typeof adaptationPatch>["slides"] = [];
  const warnings: {slide:number;reason:string;message:string}[] = [];
  const fallbackWarning = (index:number) => ({slide:index+1,reason:'plan_adaptation_fallback',message:'Сохранён утверждённый план: предложенная адаптация не принята или недоступна. Заполнение учитывает выбранный макет; проверьте результат.'});
  const starts = Array.from({length: Math.ceil(targets.length / 4)}, (_, i) => i * 4);
  await parallelSlides(starts, signal, async (start, _index, signal) => {
    const batchAllowed = new Set(allowed);
    signal.throwIfAborted();
    const batch = targets.slice(start, start + 4);
    const images: string[] = [];
    const fieldKeys = new Set<string>();
    const selected = batch.map((index) => {
      const chosen = input.slides[index];
      const layout = p.analysis.layouts.find(
        (l: Layout) => l.id === chosen.sourceLayoutId,
      ) as Layout;
      if (!layout?.usable) throw new Error("Selected layout unavailable");
      const fields = layouts[layout.id]?.slots || layout.slots;
      for (const field of fields) fieldKeys.add(field.key);
      for (const chart of layout.charts || []) fieldKeys.add(chart.key);
      // Structural counts are writing instructions, not researched statistics.
      [index + 1, fields.length, (layout.charts || []).length].forEach((n) =>
        batchAllowed.add(String(n)),
      );
      const image = join(
        folder,
        "source",
        "previews",
        `slide-${layout.index}.png`,
      );
      if (!images.includes(image)) images.push(image);
      return {
        index,
        sourceLayoutId: chosen.sourceLayoutId,
        title: chosen.title,
        brief: chosen.brief,
        imageIndex: images.indexOf(image),
        layout: {
          name: layout.name,
          fields: fields.map((s: any) => ({
            key: s.key,
            role: s.role,
            sample: s.text,
            fontSize: s.size,
            maxChars: s.maxChars,
            textRegion: s.textRegion,
            box: { x: s.x, y: s.y, w: s.w, h: s.h },
            cell: s.cell,
          })),
          charts: layout.charts,
        },
      };
    });
    const payload = {
      topic: p.brief!.topic,
      userMaterials: p.brief!.sourceText,
      researchEvidence: evidence,
      outline: input.slides.map((s, index) => ({
        index,
        title: s.title,
        brief: s.brief,
      })),
      selected,
    };
    const path = join(
      folder,
      "plan-adaptation",
      digest({
        version: 5,
        source: p.analysis.sha256,
        prepared: p.analysis.preparedSha256,
        payload,
      }) + ".json",
    );
    let result: ReturnType<typeof adaptationPatch> | undefined;
    try {
      const cached = await readJson(path);
      result = adaptationPatch(
        cached,
        input,
        batch,
        batchAllowed,
        fieldKeys,
      );
      for (const index of cached.fallbackIndices || []) if(batch.includes(index)) warnings.push(fallbackWarning(index));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (!result) {
      try {
      result = await model({
        stage: `plan-adapt-${start + 1}`,
        folder,
        signal,
        images,
        prompt: `Адаптируй содержание плана к макетам, которые выбрал пользователь. Повторного подтверждения не будет. Верни {slides:[{index,sourceLayoutId,title,brief}]} только для selected. Индексы, порядок, число слайдов, sourceLayoutId и тему менять запрещено. Заголовок и brief — замысел пользователя: сохрани смысл, но приспособь под реальные группы полей, число блоков, таблицы, графики и вместимость нового макета. При большом числе замен учитывай весь outline, убирай ненужные повторы, сохраняй связность. В brief конкретно укажи распределение содержания между группами нового макета; основной текст пиши для последующего заполнения, не пытайся заполнить PPTX сейчас. Изображения соответствуют imageIndex. Цифры и текст исходного макета — только примеры. Не придумывай факты или статистику ради заполнения. Для метрик используй подходящие данные researchEvidence/userMaterials; если их нет, сформулируй требование найти подходящий показатель по теме, не выдумывай значение. Новые темы, реальные продукты, спикеров и команды без данных не добавляй. Сохрани важные факты и оговорки, сократи объём под композицию, не уменьшай шрифт и не меняй геометрию.`,
        payload,
        validate: (raw) =>
          adaptationPatch(raw, input, batch, batchAllowed, fieldKeys, true),
      });
      } catch(error) {
        signal.throwIfAborted();
        if (!(error instanceof HttpError) || !['llm_invalid_json','llm_truncated','llm_timeout'].includes(error.code)) throw error;
        result = {slides:[],rejected:[]};
      }
    }
    const missing = batch.filter(index=>!result!.slides.some(s=>s.index===index));
    if (missing.length) {
      try {
        const repaired = await model({
          stage: `plan-adapt-missing-${start+1}`, folder, signal, images,
          prompt: 'Доработай ТОЛЬКО selected. Для отклонённых строк причина указана в rejected; остальные нужные индексы отсутствовали или ответ был недоступен. Сохрани index и sourceLayoutId. Верни {slides:[{index,sourceLayoutId,title,brief}]}. Используй материалы и выбранные поля макетов; не добавляй факты. Все перечисленные индексы обязательны.',
          payload: {...payload,selected:selected.filter(s=>missing.includes(s.index)),requiredIndices:missing,rejected:result.rejected},
          validate: raw=>adaptationPatch(raw,input,missing,batchAllowed,fieldKeys,true),
        });
        result.slides.push(...repaired.slides);
      } catch (error) {
        signal.throwIfAborted();
        if (!(error instanceof HttpError) || !['llm_invalid_json','llm_truncated','llm_timeout'].includes(error.code)) throw error;
      }
      for (const index of missing.filter(index=>!result!.slides.some(s=>s.index===index))) {
        const {sourceLayoutId,title,brief}=input.slides[index];
        result.slides.push({index,sourceLayoutId,title,brief});
        warnings.push(fallbackWarning(index));
      }
    }
    await writeJson(path, {...result,fallbackIndices:warnings.filter(w=>batch.includes(w.slide-1)).map(w=>w.slide-1)});
    patches.push(...result.slides);
    p.progress = { done: patches.length, total: targets.length };
  }, 3);
  signal.throwIfAborted();
  p.plan = mergeAdaptation(input, patches);
  p.revision++;
  p.planAdaptation = { version: 1, baseline: structuredClone(p.plan), warnings };
  delete p.contract;
  delete p.result;
  delete p.visuals;
  await writeJson(join(folder, `plan-r${p.revision}.json`), p.plan);
  p.status = "plan_ready";
  delete p.progress;
  await saveProject(
    p,
    `План автоматически адаптирован: ${targets.length} слайдов. Выбранные макеты и порядок сохранены`,
  );
  return true;
}
