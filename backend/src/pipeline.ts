import {layoutSelectionPolicy, layoutEditability, preferEditableLayouts} from './layout-selection.js';
import { materialFacts } from "./llm-context.js";
import { sourceRecoveryVersion } from './template-version.js';
import { sourceLayoutMetadata, mergeSourceLayoutMetadata } from './source-metadata.js';
import { templateTextPolicyPrompt } from './template-text-policy.js';
import { planQualityWarnings } from "./plan-quality.js";
import { engineContext } from './runtime-context.js';
import { alignmentPrompt, selectedAlignments } from './text-alignment.js';
import { recoverSlideWithWarnings } from './slide-fallback.js';
import { reviewFinalCopy } from './final-copy-review.js';
import { qualityWarnings, uniqueWarnings } from './quality-warnings.js';
import { copyOutputSchema } from "./copy-output-schema.js";
import { createReviewBatch } from "./review-batch.js";
import { validateResearchCoverage } from "./research-coverage.js";
import { parallelSlides } from "./parallel-slides.js";
import { createNativePool } from "./native-pool.js";
import { layoutConflictCandidates, earlyLayoutRepairIssues } from './layout-conflicts.js';
import { createFitBatch } from "./fit-batch.js";
import { copyCacheFingerprint, legacyCopyFingerprints, acceptCopyWarnings, unresolvedCopyIssues } from "./copy-cache.js";
import { repairSemanticGroups, groundMetricPairs, readingGroups } from "./semantic-groups.js";
import { numericReviewIssues } from './numeric-review.js';
import { repairGeometry, geometryRepairExhausted } from "./geometry-repair.js";
import {
  rebuildSlide,
  shouldRebuild,
  rebuildVersion,
  needsIndividualRepairPreview,
  rebuildLayout,
  proposalFromRebuildAttempt,
} from "./slide-rebuild.js";
import { adaptSelectedPlan } from "./plan-adaptation.js";
import { imageOnlyIssue, restoreUnsafeImages } from "./image-export.js";
import { createVisualSession, prepareVisualPlan } from "./visuals.js";
import { visualAnalysisPrompt, modelVisualSlots } from "./visual-types.js";
import { copyFile, mkdir, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import {
  contentReviewPrompt,
  contentReviewVersion,
  currentReviewHistory,
  parseContentReview,
} from "./content-review.js";
import { runNativeSandbox, nativeSandboxRevision } from "./sandbox.js";
import { repairContext as loadRepairContext } from './repair-context.js';
import { HttpError } from "./errors.js";
import { retryTransient } from "./retry.js";
import {
  renderWithRepair,
  renderAdvisories,
  digest,
  rejections,
  repeatedGeometryIssues,
  mergeRepairCandidate,
  metricRepairIssues,
  holisticRepairIssues,
  semanticRepairIssues,
  currentFitHistory,
  renderedRepairImage,
  repairAnalysis,
} from "./repair.js";
import { projectDir, readJson, writeJson, writeDiagnostic, saveProject } from "./store.js";
import { llmJson } from "./llm.js";
import {
  contextVersion,
  descriptionContext,
  fillContext,
  compactCopy,
  compactTemplate,
  selectFacts,
  deckOutline,
  needsRepairImage,
} from "./llm-context.js";
import {
  regularTables,
  tablePlanJsonSchema,
  tableContentPolicy,
  validateTablePlans,
  applyTablePlans,
  tableRowRepairIssues,
} from "./table-structure.js";
import {
  researchStamp,
  researchText,
  searchResearch,
  researchContext,
  numericEvidenceCount,
  mergeResearch,
  reusableResearch,
} from "./research.js";
import {
  planSchema,
  validateSemantics,
  bindSemanticsToPlan,
  validateCopy,
  nativeSlide,
  numbers,
  type Project,
  type Layout,
} from "./domain.js";

const nativePool = createNativePool(
  Math.max(
    1,
    Math.min(4, Math.floor(Number(process.env.PPTX_CONCURRENCY) || 4)),
  ),
);
const batchedFit = createFitBatch(
  (action, folder, extra, signal) =>
    nativeDirect(action, folder, extra, signal, true),
  100,
  nativePool,
);
const reviewContent = createReviewBatch();
export async function native(
  action: string,
  folder: string,
  extra: Record<string, any> = {},
  signal?: AbortSignal,
) {
  if (
    action === "fit" &&
    !extra.repairOptions &&
    Object.keys(extra).length === 1 &&
    extra.slides?.length === 1
  )
    return batchedFit(folder, extra.slides[0], signal);
  return nativeDirect(action, folder, extra, signal);
}
async function nativeDirect(
  action: string,
  folder: string,
  extra: Record<string, unknown> = {},
  signal?: AbortSignal,
  insidePool = false,
) {
  return retryTransient(
    async () => {
      const started = Date.now();
      let executionStarted = started;
      const documents:Array<{directory:string;files:string[]}>=[];
      const process = async () => {
        signal?.throwIfAborted();
        executionStarted = Date.now();
        return runNativeSandbox({ action, folder, ...extra, ...(engineContext()?.includeNotes && action==='export' ? {withNotes:true}: {}) }, 240000,
          (directory,files)=>documents.push({directory,files}),undefined,{signal});
      };
      const result: any = await (insidePool ? process() : nativePool(process, action==='thumbnails'));
      const persistedAt=Date.now();
      // Release CPU/render capacity before waiting for the remote database.
      // The caller still receives no success until every document is durable.
      for(const document of documents)await engineContext()?.saveNativeDocuments?.(document.directory,document.files);
      await writeDiagnostic(
        join(folder, "performance", `${Date.now()}-${randomUUID()}.json`),
        {
          action,
          durationMs: Date.now() - started,
          queueMs: executionStarted - started,
          executionMs: Date.now() - executionStarted,
          nativeProcessMs:persistedAt-executionStarted,
          persistenceMs:Date.now()-persistedAt,
          slideCount: Array.isArray(extra.slides)
            ? extra.slides.length
            : undefined,
        },
      );
      if (result.error) {
        const code =
          result.errorType === "TimeoutExpired" ? "pptx_timeout" : result.error;
        throw new HttpError(422, "Обработчик PPTX: " + code, code, {
          trace: result.trace,
          issues: result.issues,
        });
      }
      return result;
    },
    {
      signal,
      retryable: (error) =>
        error instanceof HttpError &&
        ["pptx_timeout", "pptx_runtime_failed", "pptx_render_failed"].includes(
          error.code,
        ),
      onRetry: () =>
        writeJson(
          join(folder, "recovery", `${Date.now()}-${randomUUID()}.json`),
          {
            action,
            reason: "transient_native_failure",
            automaticRetry: true,
          },
        ),
    },
  );
}

// Refresh parser eligibility without changing the user's selected layouts.
async function ensureSourceRecovery(p: Project, signal: AbortSignal) {
  if (p.analysis?.recoveryVersion === sourceRecoveryVersion) return;
  const folder = join(projectDir(p.id), "source");
  const sha = createHash("sha256")
    .update(await readFile(join(folder, "source.pptx")))
    .digest("hex");
  if (p.analysis?.sha256 && p.analysis.sha256 !== sha)
    throw new HttpError(
      409,
      "Исходный PPTX изменился. Нельзя применить к нему сохранённый план.",
      "pptx_source_changed",
    );
  await checkpoint(
    p,
    "analyzing_source",
    "Проверяем старый разбор шаблона с обновлённым восстановлением",
  );
  p.analysis = await native("analyze", folder, {}, signal);
  await saveProject(p, "Разбор шаблона обновлён автоматически");
}
async function checkpoint(
  p: Project,
  status: string,
  message: string,
  done?: number,
  total?: number,
) {
  p.status = status;
  p.progress = done === undefined ? undefined : { done, total: total! };
  await saveProject(p, message);
}
export async function analyzeSource(p: Project, signal: AbortSignal) {
  await checkpoint(
    p,
    "analyzing_source",
    "Читаем объекты PPTX и создаём изображения исходных слайдов",
  );
  p.analysis = await native(
    "analyze",
    join(projectDir(p.id), "source"),
    {},
    signal,
  );
  signal.throwIfAborted();
  await checkpoint(
    p,
    "ready",
    `Шаблон разобран. Доступно макетов: ${p.analysis.layouts.filter((l: Layout) => l.usable).length} из ${p.analysis.layouts.length}`,
  );
}
export function validatePlan(p: Project, raw: unknown) {
  const plan = planSchema.parse(raw);
  const ids = plan.slides.map((s) => s.id).filter(Boolean);
  if (new Set(ids).size !== ids.length)
    throw new HttpError(
      422,
      "Слайды должны иметь уникальные идентификаторы",
      "plan_duplicate_id",
    );
  if (plan.slides.length !== p.brief!.count)
    throw new HttpError(
      422,
      "Количество слайдов не совпадает с заданием",
      "plan_count",
    );
  for (const slide of plan.slides) {
    if (
      !p.analysis.layouts.some(
        (l: Layout) => l.id === slide.sourceLayoutId && l.usable,
      )
    )
      throw new HttpError(
        422,
        "Выбран недоступный макет",
        "layout_unavailable",
      );
  }
  return plan;
}
export async function researchProject(p: Project, signal: AbortSignal, force = false) {
  try {
    await researchProjectStrict(p,signal,force);
    delete p.researchWarning;
  } catch (error) {
    signal.throwIfAborted();
    p.researchWarning = 'Поиск или проверка источников не завершились. Использованы доступные материалы; проверьте факты и ссылки.';
    await saveProject(p,p.researchWarning);
  }
}
async function researchProjectStrict(p: Project, _signal: AbortSignal, _force = false) {
  if (p.brief) p.brief.webSearch = false;
}
export function projectFacts(p: Project) {
  const found =
    p.brief?.webSearch && p.research?.stamp === researchStamp(p.brief)
      ? researchText(p.research)
      : "";
  return `${p.brief!.topic}\n${p.brief!.sourceText}${found ? "\nФакты из веб-поиска с источниками:\n" + found : ""}`;
}
export async function generatePlan(p: Project, signal: AbortSignal) {
  await ensureSourceRecovery(p, signal);
  await researchProject(p, signal);
  const facts = projectFacts(p);
  await checkpoint(
    p,
    "planning",
    "Модель составляет план и предлагает исходные макеты",
  );
  const plan = await llmJson({
    stage: "plan",
    folder: projectDir(p.id),
    signal,
    prompt: `Составь план презентации: ровно count слайдов ВКЛЮЧАЯ титульный. Выбирай только usable layouts. Один макет можно повторять. Длина заголовков и объём смысла должны соответствовать полям выбранного макета. Если исходных чисел нет, выбирай текстовые макеты без диаграмм и крупных обязательных метрик. Количество смысловых блоков макета должно соответствовать утверждаемым тезисам: не выбирай процесс из трёх шагов для двух действий. Не добавляй третий шаг только ради макета. Для каждой выбранной метрики должен существовать подходящий числовой факт из материалов с точным предметом, периодом и единицами. Учитывай вместимость поля: крупный шрифт часто допускает только короткое значение. Не выбирай числовой макет с расчётом заполнить его словами или тире. Порядковые номера структуры допустимы. Не переносить факты из примеров шаблона. Для каждого visualSlots выбранного макета добавь images:[{slotIndex,kind,background,description}]. kind: photo, illustration, diagram (процесс/связи), chart (сравнение подтверждённых величин), interface (концепт экрана). Выбери сюжет по смыслу слайда, без переноса фактов из образца. Предпочитай изолированные объекты без фона (ориентир 80–90% подходящих мест, не жёсткая квота). background=transparent по умолчанию; opaque только для целостной сцены, окружения или requiresOpaque=true. Схемы должны показывать реальные связи из тезисов, диаграммы — только данные материалов; без данных предложи diagram. description — коротко что именно будет изображено, на языке презентации. Ответ {"slides":[{"sourceLayoutId":"slide_1","title":"...","brief":"Что именно раскрывает этот слайд, конкретные факты из материалов","images":[]}]}.` + '\n' + layoutSelectionPolicy,
    payload: {
      topic: p.brief!.topic,
      count: p.brief!.count,
      maxGeneratedImages: engineContext()?.maxGeneratedImages ?? 3,
      userSource: p.brief!.sourceText,
      researchEvidence: p.brief!.webSearch ? p.research?.evidence || [] : [],
      layouts: preferEditableLayouts(p.analysis.layouts.filter((l: Layout) => l.usable))
        .map((l: Layout) => ({
          id: l.id,
          name: l.name,
          editability: layoutEditability(l),
          charts: l.charts,
          tables: [...new Set(l.slots.filter(s=>s.cell).map(s=>s.shapeId))].map(shapeId=>({shapeId,cells:l.slots.filter(s=>s.shapeId===shapeId).length})),
          requiresRepair: !!l.requiresRebuild,
          compositionFamily: l.slots.map(s=>[s.cell?'cell':s.role,Math.round(s.x/40),Math.round(s.y/40),Math.round(s.w/40),Math.round(s.h/40)]),
          visualSlots: (l.imageSlots || l.visualSlots || []).filter((slot:any) => !slot.protected && !['icon','logo','decoration','footer','page_number'].includes(slot.sourceRole)).map((slot:any, slotIndex:number) => ({slotIndex, requiresOpaque:!!slot.requiresOpaque, aspectRatio:slot.aspectRatio})),
          fields: l.slots.map((s) => ({
            key: s.key,
            text: s.text.slice(0,120),
            role: s.role,
            maxChars: Math.min(s.maxChars,s.textFit?.targetChars??Infinity),
            fontSize: s.size,
          })),
        })),
    },
    validate: (value) => validatePlan(p, value),
  });
  p.planWarnings = planQualityWarnings(plan, facts, (p.research?.evidence || []).map(e => e.id), engineContext()?.locale);

  p.revision++;
  p.plan = { slides: plan.slides.map((s) => ({ ...s, id: randomUUID() })) };
  p.planAdaptation = { version: 1, baseline: structuredClone(p.plan) };
  delete p.contract;
  delete p.result;
  await writeJson(join(projectDir(p.id), `plan-r${p.revision}.json`), p.plan);
  await checkpoint(
    p,
    "plan_ready",
    "Проверьте план и выберите макет для каждого слайда",
  );
}
const semanticPrompt = `Опиши ШАБЛОН независимо от новой темы. intent описывает назначение поля (например «короткая подпись метрики»), а НЕ готовый текст и НЕ новые действия или факты по теме. Не придумывай содержание для недостающих блоков. Строки одного заголовка/абзаца являются одной group; required=true достаточно у первой строки, остальные строки продолжения обычно required=false. Не заставляй заполнять каждую строку лишними словами. Порядковые номера шагов имеют role=step_number. Проанализируй сохранённое изображение выбранного исходного макета. Точная геометрия и поля получены парсером и являются неизменным источником истины. Опиши смысл и визуальную структуру: иерархию, связанные группы (например число+подпись+единица: эти поля ОДНОЙ карточки должны иметь одинаковый group, а не отдельные group по роли), предназначение каждого текстового поля в исходной композиции, без добавления шагов в утверждённый план. Все поля slots должны встретиться ровно один раз, включая мелкие подписи. Не добавляй несуществующие поля. Слова и цифры шаблона — образцы, их необходимо заменить по новой теме. preserve разрешён ТОЛЬКО для фирменных надписей, декора и номера страницы, никогда для старой статистики/названия темы. Подписи спикера, должности и регалии — необязательные поля required=false: исходный образец не доказывает наличие спикера в новой презентации. clear — только необязательная подпись; основные заголовки и тезисы обязательны. role: title, body, metric, metric_label, unit, brand, decoration, page_number, footer. Ответ {"composition":"Описание композиции и взаимосвязей","fields":[{"key":"s2","role":"title","group":"hero","intent":"Назначение поля в новой презентации","action":"replace","required":true}],"charts":[{"key":"chart5","intent":"Какие данные здесь нужны"}]}.`;
export async function adaptPlan(p: Project, signal: AbortSignal) {
  await writeJson(join(projectDir(p.id), `plan-r${p.revision}.json`), p.plan);
  await ensureSourceRecovery(p, signal);
  await adaptSelectedPlan(p, signal, async () =>
    sourceLayoutMetadata(p) ?? await native("layout_metadata", join(projectDir(p.id), "source"), {}, signal),
  );
}
export async function assembleAndDescribe(p: Project, signal: AbortSignal, stream = false) {
  await adaptPlan(p, signal);
  const folder = projectDir(p.id),
    assembled = join(folder, `assembled-r${p.revision}`);
  await mkdir(assembled, { recursive: true });
  await checkpoint(
    p,
    "assembling",
    "Собираем утверждённые макеты в отдельный PPTX",
  );
  const ids = p.plan!.slides.map((s) => s.sourceLayoutId),
    selection = createHash("sha256")
      .update(
        JSON.stringify({
          ids,
          source: p.analysis.sha256,
          prepared: p.analysis.preparedSha256,
          recoveryVersion: sourceRecoveryVersion,
        }),
      )
      .digest("hex");
  let sourceMeta = sourceLayoutMetadata(p);
  if (!sourceMeta) {
    const metadata=await native("layout_metadata",join(folder,"source"),{},signal);
    p.analysis.layouts=mergeSourceLayoutMetadata(p.analysis.layouts,metadata);
    sourceMeta=Object.fromEntries(p.analysis.layouts.map((layout:Layout)=>[layout.id,layout]));
  }
  const designAbort=new AbortController(),designSignal=AbortSignal.any([signal,designAbort.signal]);
  const semanticRequests=new Map<string,Promise<any>>(),designs=new Map<number,Promise<any>>();
  const ensureDesign=(i:number)=>{
    let pending=designs.get(i);
    if(!pending){
      pending=(async()=>{
        const layout=sourceMeta[ids[i]] as Layout;
        if(!layout.usable || layout.requiresRebuild)return;
      const fingerprint = createHash("sha256")
        .update(
          JSON.stringify({
            version: 11,
            source: p.analysis.sha256,
            prepared: p.analysis.preparedSha256,
            recoveryVersion: sourceRecoveryVersion,
            layoutId: ids[i],
          }),
        )
        .digest("hex");
      const path = join(assembled, `semantic-layout-${fingerprint}.json`);
      let request=semanticRequests.get(fingerprint);
      if(!request){
        request=(async()=>{
      let cached: any;
      try {
        cached = await readJson(path);
      } catch {}
      const semantics =
        cached?.fingerprint === fingerprint
          ? validateSemantics(cached.value, layout)
          : await llmJson({
              stage: `describe-${i + 1}`,
              folder,
              signal:designSignal,
              prompt:
                semanticPrompt + '\n' + templateTextPolicyPrompt + '\n' + alignmentPrompt +
                '\nЕсли на изображении видны ошибочные наложения текста, верни layoutIssues:[{keys:[ключи конфликтующих объектов],instruction:как развести блоки, сохранив дизайн}]. conflictCandidates — лишь пересечения рамок, не доказанные ошибки. Подтверждай только реально нечитаемое наложение текста/подписей на текст или график. Фон за текстом, свободное место внутри большой рамки и намеренное декоративное наложение ошибкой не являются. Если конфликтов нет, layoutIssues:[]. Крупные номера слайда/раздела — page_number, а не фактическая метрика.' +
                "\n" +
                visualAnalysisPrompt +
                "\nОписывай компактно: composition до двух предложений, intent — краткое назначение поля (до 10 слов), без повторения координат и исходного текста.",
              payload: {
                conflictCandidates: layoutConflictCandidates(layout),
                native: {
                  ...descriptionContext(layout),
                  visualSlots: modelVisualSlots(layout.visualSlots || []),
                },
                dimensions: {width:p.analysis.width,height:p.analysis.height,unit:"pt"},
              },
              images: [join(folder,"source","previews",`slide-${layout.index}.png`)],
              validate: (value) => validateSemantics(value, layout),
            });
        await writeJson(path, {fingerprint,value:semantics});
        return semantics;
        })();
        semanticRequests.set(fingerprint,request);
      }

        return request;
      })();
      designs.set(i,pending);
    }
    return pending;
  };
  // Source geometry and previews are already verified at upload. Describing
  // exactly the selected designs does not depend on copying them into a ZIP.
  const designReady=parallelSlides(ids,designSignal,async(_id,i)=>{await ensureDesign(i);});
  void designReady.catch(()=>{});
  try {
  let analysis: any;
  try {
    const previous = await readJson(join(assembled, "assembly.json"));
    const sha = createHash("sha256")
      .update(await readFile(join(assembled, "source.pptx")))
      .digest("hex");
    if (previous.cacheVersion === 2 && previous.selection === selection && previous.sha === sha)
      analysis = await readJson(join(assembled, "analysis.json"));
  } catch {}
  if (!analysis) {
    const selected = await native(
      "assemble",
      join(folder, "source"),
      {
        layoutIds: ids,
        output: assembled,
      },
      signal,
    );
    signal.throwIfAborted();
    await copyFile(
      join(assembled, "presentation.pptx"),
      join(assembled, "source.pptx"),
    );
    analysis = selected.analysis;
    await mkdir(join(assembled, "previews"), {recursive:true});
    await Promise.all(ids.map((id,i)=>copyFile(
      join(folder,"source","previews",`slide-${p.analysis.layouts.find((l:any)=>l.id===id).index}.png`),
      join(assembled,"previews",`slide-${i}.png`))));
    await copyFile(join(assembled,"source-preview.pdf"),join(assembled,"previews","presentation.pdf"));
    signal.throwIfAborted();
    await writeJson(join(assembled, "assembly.json"), {
      cacheVersion: 2, selection,
      sha: analysis.sha256,
    });
  }
  if (analysis.layouts.length !== ids.length)
    throw new HttpError(
      422,
      "Число собранных слайдов изменилось",
      "assembly_count",
    );

  const visualMeta = Object.fromEntries(analysis.layouts.map((l:any,i:number)=>[l.id,sourceMeta[ids[i]]]));
  for (const layout of analysis.layouts) {
    layout.visualSlots = structuredClone(visualMeta[layout.id]?.visualSlots || []);
    layout.slots = structuredClone(visualMeta[layout.id]?.slots || layout.slots);
  }
  const contract: any = {
    version: "nerpa-native-template/1",
    recoveryVersion: sourceRecoveryVersion,
    revision: p.revision,
    sourceSha256: p.analysis.sha256,
    assembledSha256: analysis.sha256,
    dimensions: { width: analysis.width, height: analysis.height, unit: "pt" },
    styleProfile: analysis.styleProfile,
    plan: p.plan,
    descriptionComplete: false,
    slides: analysis.layouts.map((layout:any,i:number)=>({index:i,originalSourceLayoutId:ids[i],assembledLayoutId:layout.id,native:layout})),
  };
  p.contract = contract;
  let described = 0;
  const describe = async (layout:Layout, i:number, signal:AbortSignal) => {
      signal.throwIfAborted();
      if (!layout.usable) layout.requiresRebuild = true;
      if (layout.requiresRebuild) {
        contract.slides[i] = {
          index: i,
          originalSourceLayoutId: ids[i],
          assembledLayoutId: layout.id,
          native: layout,
          semantics: {
            composition: "Исходные рамки требуют восстановления",
            fields: [],
            charts: [],
            images: [],
          },
        };
        if(!stream) await checkpoint(
          p,
          "analyzing",
          `Макет ${i + 1} будет восстановлен при заполнении`,
          ++described,
          ids.length,
        );
        return;
      }
      const semantics=validateSemantics(structuredClone(await ensureDesign(i)),layout);
      const boundSemantics = bindSemanticsToPlan(
        semantics,
        layout,
        p.plan!.slides[i],
      );
      contract.slides[i] = {
        index: i,
        originalSourceLayoutId: ids[i],
        assembledLayoutId: layout.id,
        native: layout,
        semantics: boundSemantics,
      };
      if(!stream) await checkpoint(
        p,
        "analyzing",
        `Описано макетов: ${++described} из ${ids.length}`,
        described,
        ids.length,
      );
    };
  const requests = new Map<number,Promise<any>>();
  const ensure = (i:number) => {
    let request=requests.get(i);
    if(!request){request=describe(analysis.layouts[i],i,signal).then(()=>contract.slides[i]);requests.set(i,request);}
    return request;
  };
  const finish = parallelSlides(analysis.layouts as Layout[],signal,async(_layout,i)=>{await ensure(i);}).then(async()=>{
    contract.descriptionComplete=true;
    await writeJson(join(folder, `template-r${p.revision}.json`),contract);
  });
  // The owning generation drains descriptions before releasing its lease.
  void finish.catch(()=>{});
  if(stream && engineContext()) {
    engineContext()!.describeSlide=ensure;
    engineContext()!.descriptionsReady=finish;
    return;
  }
  await finish;
  await prepareVisualPlan(p, signal, visualMeta);
  await checkpoint(p,"template_ready","Макеты описаны. Можно запустить заполнение",ids.length,ids.length);
  } catch(error) {
    designAbort.abort(error);await designReady.catch(()=>{});throw error;
  }
}
export const copyPrompt = `Заполни утверждённый слайд по JSON шаблона. Ответ {"fields":{"key":{"text":"...","evidence":["evidence-1"]}},"charts":{}}. Все поля и диаграммы обязательны по semantics; геометрия, ключи и шрифты неизменны. Исходные тексты и цифры — образцы, не факты новой темы.
readingGroups задаёт реальные связки полей. Заполняй число и подпись одной группы как единое утверждение. Не связывай соседние числовые ID: порядок ключей не является расположением на слайде. Каждая группа должна сама передавать верный показатель, а не только содержать числа из источника.
Поля needsGeometryAssistance: исходная рамка слишком узкая для содержательного текста. Напиши одну краткую законченную мысль с действием или пояснением, не заменяй абзац одиночным названием направления; лимиты textFit/maxChars здесь не являются целевым объёмом. Сервер попробует расширение, а при невозможности восстановит компоновку.
Поля с writingTargetChars: с первой попытки укладывай обычный текст в этот целевой объём, без пустых строк. Выбирай одну-две мысли, а не пересказ всех материалов. Не увеличивай текст ради полноты плана. Числа, единицы и смысл сохраняй полностью. Пиши кратко с первой попытки: план задаёт тему, а не требование перенести все факты. textFit.targetChars и textFit.lines — измеренный ориентир вместимости: для обычного текста сначала планируй длину и число строк по ним. Не заполняй рамку до предела. Для метрик сохраняй полное число и смысл, даже если ориентир мал. maxChars — ориентир исходной рамки; сервер проверяет физическую вместимость и может минимально расширить рамку в свободное место. Не рассчитывай на расширение через соседние объекты, не обрезай число или смысл ради лимита.
metric требует реального числа с evidence. Каждое число, включая год, подтверждай ID researchEvidence или точной цитатой userSource. Не выдумывай статистику, достижения, источники и автора. Запрещены тире/НД/вопрос вместо метрики. При отсутствии нужного показателя выбери другой подтверждённый факт по теме и согласованно поменяй всю группу. У рекомендаций и общих пояснений допустим evidence:[]; не записывай туда пересказ или собственный текст как цитату. Общие объяснения могут опираться на устойчивые знания без вымышленных цифр; предложения обозначай как рекомендации, не как состоявшееся внедрение.
Сохраняй субъект, единицу, период, выборку и оговорки использованных фактов. Число, подпись и единица одной group описывают один факт; распределяй смысл между ними. Можно перенести % или другую единицу в подпись/общий заголовок, сохранив полное значение и понятный субъект. Не заполняй разные карточки одинаковой метрикой. Не превращай название в непонятную одиночную букву. В сравнениях формулируй изменение словами «с … до …» или отдельными полями «До» и «После». Не заменяй сравнение дробью: она имеет иной числовой смысл. Не добавляй специальные декоративные символы, если их нет в исходном поле: шрифт шаблона может их не поддерживать. body не обязан содержать число, а короткая подпись — весь факт. Необязательную личную подпись можно очистить, если пользователь не сообщил имя.
${tableContentPolicy}
Таблицы: образец числовой оценки не требует выдумывать вероятности или баллы. Если их нет в материалах, согласованно переименуй столбцы под подтверждённые характеристики или явно рекомендованные действия. Таблицы cell/tableStructure: одна запись на строку, соседние столбцы — связанные свойства этой записи. Заголовки соответствуют субъекту, виду и единицам всех значений; нельзя смешивать несопоставимые статистики. Номер строки № не требует источника. Диаграммы: строго seriesCount рядов и pointCount категорий, все значения подтверждены.
Списки nativeTextStructure.isList: один пункт на строку без маркеров и пустых строк, не более maxItems. При autoNumbered не пиши номера, их добавляет PowerPoint. В обычном тексте paragraphCount — ориентир. При issues допустим патч только разрешённых полей и связанных групп, остальное сохраняет сервер. Верные факты и формулировки сохраняй; ошибки evidence исправляй ссылкой. Semantics приоритетнее образца. evidence содержит только ID из researchEvidence или точную цитату userSource; ID слайда плана не является источником. Пиши на языке темы и материалов пользователя; названия брендов сохраняй. Инструкции по заполнению не являются текстом презентации: не выводи служебные пояснения о проверках, числах и ограничениях.`;
export const repairPrompt = `
Исправь причину из issues, а не переписывай весь слайд. Верни патч allowedFieldKeys и repairAnalysis:{cause,action,fields}; остальные поля сохраняет сервер. unchangedContent показывает соседние значения. Используй измерения, previousDiagnosis и rejected, не повторяй неудачную стратегию. Верные факты и смысл сохраняй; если проблема только в evidence, исправь ссылку.
При переполнении по высоте сократи число строк/абзацев: сокращение отдельных слов при прежнем числе строк может не помочь. При переполнении по ширине перефразируй или перенеси строку. suggestedMaxChars — ориентир, а не разрешение обрезать слово или число. Правила единиц, смысловых групп, native-списков и строк таблицы из основного задания сохраняются. Если открыты заголовки таблицы, можно согласовать их с данными.
Первое изображение, если передано, — исходный макет, второе — точный неудачный рендер previous. При отсутствии изображения не заявляй, что видел результат. Геометрию меняет отдельный структурированный патч; здесь меняй только текст.
`;

async function prepareDeck(
  p: Project,
  signal: AbortSignal,
  onSlideReady: (index: number, slide: any) => void,
) {
  const folder = projectDir(p.id),
    assembled = join(folder, `assembled-r${p.revision}`),
    output = join(folder, `output-r${p.revision}`);
  await mkdir(output, { recursive: true });
  const sha = createHash("sha256")
    .update(await readFile(join(assembled, "source.pptx")))
    .digest("hex");
  if (sha !== p.contract.assembledSha256 || p.contract.revision !== p.revision)
    throw new HttpError(
      409,
      "Макет изменился, требуется повторный анализ",
      "contract_stale",
    );
  const source = `${projectFacts(p)}\nУтверждённый пользователем план:\n${p.plan!.slides.map((s) => s.title + "\n" + s.brief).join("\n")}`,
    slides: any[] = [],
    evidencePool = materialFacts(p.brief!.sourceText, p.brief?.webSearch && p.research?.stamp === researchStamp(p.brief) ? p.research?.evidence || [] : []);
  const frames = await readJson(join(assembled, "frames.json"));
  let completed = 0;
  let repairContext: Promise<any> | undefined;
  const repairContextFolder = join(output,'repair-context');
  // All parallel repairs share one immutable description and one blank deck
  // render. Never share across projects or assembled revisions.
  const currentRepairContext = () => (repairContext ||= (async()=>loadRepairContext({
    output:repairContextFolder,fingerprint:digest({sha,rebuildVersion,runtime:await nativeSandboxRevision()}),
    count:p.contract.slides.length,signal,
    build:()=>native('repair_context',assembled,{output:repairContextFolder},signal),
    fallback:()=>native('rebuild_context',assembled,{},signal),
  }))());
  const currentRebuildProfiles = async () => (await currentRepairContext()).profiles;
  const reconstruct = async (
    spec: any,
    i: number,
    previous: any,
    issues: any[],
    rejected: any[],
    signal: AbortSignal,
  ) => {
    await writeJson(join(output, `copy-${i}.json`), {
      data: previous,
      issues,
      rejected,
      passed: false,
      rebuildPending: true,
    });
    await checkpoint(
      p,
      "filling",
      `Восстанавливаем компоновку слайда ${i + 1}: исправляем расположение и читаемость полей`,
      completed,
      p.contract.slides.length,
    );
    const result = await rebuildSlide({
      folder,
      output,
      assembled,
      index: i,
      sha,
      layout: {...spec.native, rebuildSemantics: spec.semantics},
      approved: p.plan!.slides[i],
      topic: p.brief!.topic,
      source: projectFacts(p),
      pool: evidencePool,
      previous,
      issues,
      rejected,
      signal,
      native,
      profile: (await currentRebuildProfiles())[spec.native.id],
      blankImage: (await currentRepairContext()).blankSlides ? join(repairContextFolder,`slide-${i}.png`) : undefined,
      // The first pass is visually checked by the whole-deck export. A repair
      // requested by that render gets an individual preview before acceptance.
      deferRender: !needsIndividualRepairPreview(issues),
    }).catch(async (error) => {
      signal.throwIfAborted();
      return recoverSlideWithWarnings({output, assembled, index:i, layout:spec.native,
        title:p.plan!.slides[i].title, previous, issues:[...issues,{reason:'repair_unavailable',code:error?.code}], signal, native});
    });
    await writeJson(join(output, `copy-${i}.json`), {
      data: result.data,
      issues: [],
      rejected,
      passed: true,
      rebuildNative: result.slide,
      sourceFallback: !!result.slide.native?.preserveSource,
      needsContentReview: 'issues' in result && !result.slide.native?.preserveSource,
      rebuildNotice: result.notice,
      rebuildContext: digest({
        rebuildVersion,
        sha,
        plan: p.plan!.slides[i],
        source: projectFacts(p),
        review: contentReviewVersion,
      }),
    });
    return result.slide;
  };
  await parallelSlides<any>(
    p.contract.slides,
    signal,
    async (spec, i, signal) => {
      if(engineContext()?.describeSlide) spec=await engineContext()!.describeSlide!(i);
      spec.semantics=groundMetricPairs(spec.semantics,spec.native.slots);
      try {
      try {
        const saved = await readJson(join(output, `copy-${i}.json`));
        if (
          (saved.rebuildRequested || saved.rebuildPending) &&
          !saved.rebuildNative
        ) {
          slides[i] = await reconstruct(
            spec,
            i,
            saved.data,
            saved.issues || [],
            saved.rejected || [],
            signal,
          );
          onSlideReady(i, slides[i]);
          await checkpoint(
            p,
            "filling",
            `Проверено слайдов: ${++completed}`,
            completed,
            p.contract.slides.length,
          );
          return;
        }
        // An earlier provider/processing outage may have left only the source
        // sample. Retry normal filling; do not treat it as a new broken design
        // that requires reconstructing every source page from scratch.
        if (saved.rebuildNative && !saved.rebuildNative.native?.preserveSource) {
          const reusable =
            !saved.rebuildNotice?.warnings?.some((w: any) => w.reason === "contrast_repair_unavailable") &&
            (!saved.rebuildNative.native?.rebuild || saved.rebuildNative.native.rebuild.fingerprint ===
              (await currentRebuildProfiles())[spec.native.id]?.fingerprint) &&
            saved.rebuildContext ===
            digest({
              rebuildVersion,
              sha,
              plan: p.plan!.slides[i],
              source: projectFacts(p),
              review: contentReviewVersion,
            });
          slides[i] =
            reusable && saved.passed && !saved.issues?.length
              ? saved.rebuildNative
              : await reconstruct(
                  spec,
                  i,
                  saved.rebuildNative.native?.preserveSource ? undefined : saved.data,
                  saved.issues || [],
                  saved.rejected || [],
                  signal,
                );
          onSlideReady(i, slides[i]);
          await checkpoint(
            p,
            "filling",
            `Проверено слайдов: ${++completed}`,
            completed,
            p.contract.slides.length,
          );
          return;
        }
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
      }
      const sourceLayoutIssues=earlyLayoutRepairIssues(spec.native,spec.semantics);
      if (spec.native.requiresRebuild || sourceLayoutIssues.length) {
        slides[i] = await reconstruct(
          spec,
          i,
          { fields: {}, charts: {} },
          sourceLayoutIssues.length ? sourceLayoutIssues : [{ reason: "layout_unavailable", details: spec.native.warnings }],
          [],
          signal,
        );
        onSlideReady(i, slides[i]);
        await checkpoint(
          p,
          "filling",
          `Проверено слайдов: ${++completed}`,
          completed,
          p.contract.slides.length,
        );
        return;
      }
      const tableSource = spec.sourceNative || spec.native;
      const tables = regularTables(tableSource);
      if (tables.length) {
        signal.throwIfAborted();
        await checkpoint(
          p,
          "filling",
          `Планируем записи и строки таблиц на слайде ${i + 1}`,
          completed,
          p.contract.slides.length,
        );
        const tableFingerprint = digest({
          version: 4,
          sha,
          layout: tableSource,
          plan: p.plan!.slides[i],
          source,
        });
        const tablePath = join(output, `table-plan-${i}.json`);
        let tablePlan;
        try {
          const saved = await readJson(tablePath);
          if (saved.fingerprint === tableFingerprint)
            tablePlan = validateTablePlans(saved.data, tableSource);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
        if (!tablePlan) {
          tablePlan = await llmJson({
            stage: `fill-table-plan-${i + 1}`,
            schema: tablePlanJsonSchema(tableSource),
            folder,
            signal,
            images: [join(assembled, "previews", `slide-${i}.png`)],
            prompt: tableContentPolicy + ` Организуй данные слайда в настоящие таблицы. rows содержит описание одной полной записи со значением и единицей для последующего заполнения, а не независимые списки значений. Используй утверждённый план и факты. Ответ {"tables":[{"shapeId":число,"columns":["название столбца"],"rows":["смысл одной записи"]}]}.`,
            payload: {
              tables,
              approved: p.plan!.slides[i],
              topic: p.brief!.topic,
              userSource: p.brief!.sourceText,
              researchEvidence: selectFacts(
                evidencePool,
                p.plan!.slides[i].title + "\n" + p.plan!.slides[i].brief,
              ),
            },
            validate: (v) => validateTablePlans(v, tableSource),
          });
          await writeJson(tablePath, {
            fingerprint: tableFingerprint,
            data: tablePlan,
          });
        }
        spec.sourceNative ||= structuredClone(spec.native);
        spec.sourceSemantics ||= structuredClone(spec.semantics);
        const expanded = applyTablePlans(
          spec.sourceNative,
          spec.sourceSemantics,
          tablePlan,
        );
        spec.native = expanded.layout;
        spec.semantics = expanded.semantics;
      }
      const nativeLists: Record<string, string[]> = {};
      for (const [key, frame] of Object.entries(
        frames[spec.native.id] || {},
      ) as [string, any][]) {
        const paragraphs = (frame.paragraphs || []).filter(
          (p: any) => p.has_text,
        );
        if (
          paragraphs.length &&
          paragraphs.every((p: any) => p.bullet_text && !p.bullet_auto)
        )
          nativeLists[key] = [
            ...new Set<string>(paragraphs.map((p: any) => p.bullet_text)),
          ];
      }
      const layout: Layout = spec.native;
      const alignments = selectedAlignments(spec.semantics.fields, layout.slots);
      if (Object.keys(alignments).length) layout.textAlignment = alignments;
      else delete layout.textAlignment;
      let semantics = bindSemanticsToPlan(
        spec.semantics,
        layout,
        p.plan!.slides[i],
      );
      const path = join(output, `copy-${i}.json`);
      spec.semantics = semantics;
      const sourceOrdinal =
        p.analysis.layouts.find(
          (l: Layout) => l.id === spec.originalSourceLayoutId,
        )?.index + 1;
      signal.throwIfAborted();
      const cacheContext = { contextVersion, spec, source, plan: p.plan, sha };
      const fingerprint = copyCacheFingerprint(cacheContext);
      let originalImages: unknown;
      try {
        originalImages = (await readJson(join(assembled, `semantic-${i}.json`)))
          .value?.images;
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
      }
      const legacyFingerprints = legacyCopyFingerprints(
        cacheContext,
        originalImages,
      );
      semantics = await repairSemanticGroups({
        folder,
        output,
        assembled,
        index: i,
        layout,
        semantics,
        signal,
      });
      const semanticGroupsHash = digest(
        semantics.fields.map((f: any) => ({ key: f.key, group: f.group })),
      );
      let data: any,
        issues: any[] = [],
        rejected: any[] = [],
        passed = false,
        recheckSavedCandidate = false,
        acceptedData: any,
        reviewNotes: any[] = [],
        lastDiagnosis: any;
      try {
        const old = await readJson(path);
        if (
          old.fingerprint === fingerprint ||
          legacyFingerprints.includes(old.fingerprint)
        ) {
          data = old.data;
          if (old.geometryRepairs) layout.geometryRepairs = old.geometryRepairs;
          lastDiagnosis = old.repairAnalysis;
          acceptedData =
            old.acceptedData || (old.passed ? old.data : undefined);
          issues = currentFitHistory(
            currentReviewHistory(old.issues || [], old.contentReviewVersion),
          ).filter(
            (issue) =>
              !imageOnlyIssue(issue) &&
              !(
                issue.details?.length &&
                issue.details.every((d) => d === "rendered_font_mismatch")
              ),
          );
          rejected = currentFitHistory(
            currentReviewHistory(old.rejected || [], old.contentReviewVersion),
          ).filter(
            (issue) =>
              !imageOnlyIssue(issue) &&
              !(
                issue.details?.length &&
                issue.details.every((d) => d === "rendered_font_mismatch")
              ),
          );
          const current = validateCopy(
            data,
            layout,
            semantics,
            source,
            evidencePool,
            nativeLists,
          );
          current.issues = unresolvedCopyIssues(current.issues, current.data, old.acceptedWarnings, contentReviewVersion);
          data = current.data;
          if (current.issues.length) issues = current.issues;
          // A validator upgrade can release a rejected copy. Check that exact
          // copy with today's fitter/reviewer before buying a fresh rewrite.
          const needsContentReview =
            old.contentReviewVersion !== contentReviewVersion ||
            old.semanticGroupsHash !== semanticGroupsHash;
          recheckSavedCandidate =
            (!old.passed || needsContentReview) &&
            !issues.length &&
            !current.issues.length;
          const onlyImageFailure =
            old.issues?.length && old.issues.every(imageOnlyIssue);
          if (
            !needsContentReview &&
            (old.passed || (onlyImageFailure && acceptedData)) &&
            !issues.length
          ) {
            const checked = validateCopy(
              data,
              layout,
              semantics,
              source,
              evidencePool,
              nativeLists,
            );
            checked.issues = unresolvedCopyIssues(checked.issues, checked.data, old.acceptedWarnings, contentReviewVersion);
            if (!checked.issues.length) {
              const fit = await native(
                "fit",
                assembled,
                {
                  slides: [
                    nativeSlide(
                      checked.data,
                      layout,
                      p.plan!.slides[i].title,
                      i + 1,
                      sourceOrdinal,
                    ),
                  ],
                },
                signal,
              );
              issues = fit.issues || [];
              if (!issues.length) {
                await writeJson(path, {
                  ...old,
                  fingerprint,
                  data: checked.data,
                  issues: [],
                  passed: true,
                });
                slides[i] = nativeSlide(
                  checked.data,
                  layout,
                  p.plan!.slides[i].title,
                  i + 1,
                  sourceOrdinal,
                );
                onSlideReady(i, slides[i]);
                p.progress = {
                  done: ++completed,
                  total: p.contract.slides.length,
                };
                await saveProject(p);
                return;
              }
              rejected.push(...rejections(data, issues, "cached-fit"));
              await writeJson(path, {
                ...old,
                data,
                issues,
                rejected,
                passed: false,
              });
            }
          }
        }
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
      }
      // The deck is already in filling state. Forty-five parallel slides must
      // not queue identical progress writes before their first provider call.
      // Completed copy/geometry and lease checks remain durable below.
      for (let attempt = 0; attempt < 3; attempt++) {
        signal.throwIfAborted();
        if (data && shouldRebuild(issues,[]) && attempt>0) {
          slides[i] = await reconstruct(spec,i,data,issues,rejected,signal);
          onSlideReady(i,slides[i]);
          await checkpoint(p,'filling',`Адаптировано и проверено: ${++completed} из ${p.contract.slides.length}`,completed,p.contract.slides.length);
          return;
        }
        if (
          attempt >= 2 &&
          shouldRebuild(issues, []) &&
          (await geometryRepairExhausted(output, i, layout))
        )
          break;
        let geometryAccepted = false;
        if (data && issues.length) {
          const patchIssues = tableRowRepairIssues(
            semanticRepairIssues(
              metricRepairIssues(issues, semantics.fields),
              semantics.fields,
              rejected,
            ),
            layout,
          );
          const editableKeys = semantics.fields
            .filter(
              (f: any) =>
                f.action === "replace" &&
                patchIssues.some((issue) => issue.key === f.key),
            )
            .map((f: any) => f.key);
          const repaired = await repairGeometry({
            folder,
            output,
            assembled,
            index: i,
            title: p.plan!.slides[i].title,
            layout,
            data,
            issues,
            signal,
            native,
            textRepair: {
              keys: editableKeys,
              context: {
                approved: p.plan!.slides[i],
                userSource: p.brief!.sourceText,
                researchEvidence: selectFacts(
                  evidencePool,
                  p.plan!.slides[i].title + "\n" + p.plan!.slides[i].brief,
                  data,
                ),
                template: compactTemplate(
                  layout,
                  semantics,
                  new Set(editableKeys),
                ),
                previous: compactCopy(data, evidencePool),
              },
              validate: (candidate) =>
                validateCopy(
                  candidate,
                  layout,
                  semantics,
                  source,
                  evidencePool,
                  nativeLists,
                ),
            },
          });
          if (repaired) {
            if (repaired.data) data = repaired.data;
            issues = repaired.issues;
            lastDiagnosis = repaired.diagnosis;
            geometryAccepted = !issues.length;
            await writeJson(path, {
              fingerprint,
              data,
              issues,
              rejected,
              passed: false,
              contentReviewVersion,
              geometryRepairs: layout.geometryRepairs,
              repairAnalysis: lastDiagnosis,
            });
            // The old rejection describes the old frame. Re-measure unchanged
            // wording under the approved proposal instead of banning it forever.
            rejected = rejected.map((r: any) =>
              repaired.diagnosis.fields.includes(r.key) &&
              r.reason === "overflow"
                ? { ...r, geometrySuperseded: true }
                : r,
            );
          }
        }
        // Both local geometry attempts failed. Continue with the AI's native
        // slide recovery instead of buying another rewrite of the same frame.
        if (!geometryAccepted && shouldRebuild(issues, []) && await geometryRepairExhausted(output, i, layout)) break;
        const previous = data;
        issues = tableRowRepairIssues(
          semanticRepairIssues(
            metricRepairIssues(issues, semantics.fields),
            semantics.fields,
            rejected,
          ),
          layout,
        );
        issues = holisticRepairIssues(issues, semantics.fields, rejected);
        const holistic = issues.some(
          (issue) => issue.reason === "slide_context_changed",
        );
        const failedImage =
          previous && issues.length
            ? await renderedRepairImage(output, i, previous)
            : undefined;
        const context = fillContext({
          topic: p.brief!.topic,
          userMaterials: p.brief!.sourceText,
          plan: p.plan!,
          index: i,
          layout,
          semantics,
          dimensions: p.contract.dimensions,
          pool: evidencePool,
          frames: frames[spec.native.id] || {},
          previous,
          issues,
          rejected,
          expansion: attempt,
          failedRenderAvailable: !!failedImage,
          diagnosis: lastDiagnosis,
        });
        const showImage = issues.length > 0 && needsRepairImage(issues);
        const candidate =
          geometryAccepted || (recheckSavedCandidate && attempt === 0)
            ? { data, repairAnalysis: undefined }
            : await llmJson({
                stage: `fill-${i + 1}-${attempt}`,
                schema: copyOutputSchema(layout, issues, semantics.fields),
                ...(layout.slots.length >= 18
                  ? { reasoning: "medium" as const }
                  : {}),
                folder,
                signal,
                useVisionModel: true,
                prompt:
                  copyPrompt +
                  (issues.length ? repairPrompt : "") +
                  (holistic
                    ? "\nЛокальные исправления уже конфликтовали. Сейчас реши задачу целиком по слайду: сначала проверь разные субъекты и показатели всех карточек, затем распредели число/единицу/подпись по доступным полям. Не копируй соседнюю метрику ради вместимости. Отдай согласованный патч и диагноз. При нехватке места предпочти короткую единицу в подписи, сохрани содержательное число. Не пытайся помещать весь исходный факт в одну рамку."
                    : ""),
                ...(holistic ? { repair: { mode: "whole-slide" } } : {}),
                images: showImage
                  ? [
                      join(assembled, "previews", `slide-${i}.png`),
                      ...(failedImage ? [failedImage] : []),
                    ]
                  : [],
                payload: {
                  ...context,
                  imageRoles: showImage
                    ? failedImage
                      ? ["source_layout", "failed_render_of_previous"]
                      : ["source_layout"]
                    : [],
                },
                validate: (v) => ({
                  ...validateCopy(
                    mergeRepairCandidate(v, previous, issues),
                    layout,
                    semantics,
                    source,
                    evidencePool,
                    nativeLists,
                  ),
                  repairAnalysis: issues.length
                    ? repairAnalysis(
                        v,
                        issues.flatMap((issue) =>
                          issue.key ? [issue.key] : [],
                        ),
                      )
                    : undefined,
                }),
              });
        lastDiagnosis = candidate.repairAnalysis || lastDiagnosis;
        const checked = validateCopy(
          candidate.data,
          layout,
          semantics,
          source,
          evidencePool,
          nativeLists,
        );
        data = checked.data;
        const numericReview=numericReviewIssues(checked.issues);
        issues = [...numericReview.issues, ...repeatedGeometryIssues(data, rejected)];
        // repairGeometry has just checked this exact unchanged copy and the
        // accepted frame proposal with the native fitter. Do not launch the
        // same sandbox again; factual review and final PDF checks still run.
        if (
          !geometryAccepted &&
          !issues.some((issue) =>
            ["chart_dimensions", "chart_range"].includes(issue.reason),
          )
        ) {
          const fit = await native(
            "fit",
            assembled,
            {
              slides: [
                nativeSlide(
                  data,
                  layout,
                  p.plan!.slides[i].title,
                  i + 1,
                  sourceOrdinal,
                ),
              ],
            },
            signal,
          );
          issues.push(...fit.issues);
        }
        if (!issues.length) {
          const review = await reviewContent({
            stage: `content-review-${i + 1}-${attempt}`,
            folder,
            signal,
            prompt: contentReviewPrompt,
            payload: {
              topic: p.brief!.topic,
              userSource: p.brief!.sourceText,
              researchEvidence: selectFacts(
                evidencePool,
                p.plan!.slides[i].title + "\n" + p.plan!.slides[i].brief,
                data,
                attempt,
              ),
              approved: p.plan!.slides[i],
              semantics: { fields: semantics.fields, charts: semantics.charts },
              tableStructure: layout.tableStructure,
              tableCells: layout.slots
                .filter((s) => s.cell)
                .map((s) => ({ key: s.key, table: s.shapeId, cell: s.cell })),
              data: compactCopy(data, evidencePool),
              readingGroups: readingGroups(semantics,layout.slots,data),
              numericWarnings: numericReview.warnings.map(({key,text,unsupportedNumbers})=>({key,text,unsupportedNumbers})),
              previouslyAcceptedData: compactCopy(acceptedData, evidencePool),
              repairHistory: rejected
                .slice(-12)
                .filter((e) => e.reason === "content_mismatch")
                .map(({ key, value, message }) => ({ key, value, message })),
            },
            validate: (v) => parseContentReview(v, data, projectFacts(p), semantics.fields),
          });
          reviewNotes = review.notes;
          issues = review.issues;
        }
        rejected.push(...rejections(data, issues, "fill"));
        passed = !issues.length;
        await writeJson(path, {
          fingerprint,
          data,
          issues,
          rejected,
          passed,
          acceptedData: passed ? data : acceptedData,
          ...(passed && numericReview.warnings.length ? {acceptedWarnings:acceptCopyWarnings(data,numericReview.warnings,contentReviewVersion)} : {}),
          contentReviewVersion,
          semanticGroupsHash,
          citationWarnings:
            [...("citationWarnings" in candidate ? candidate.citationWarnings : []),...numericReview.warnings],
          reviewNotes,
          repairAnalysis: lastDiagnosis,
          geometryRepairs: layout.geometryRepairs,
        });
        if (passed) break;
        await checkpoint(
          p,
          "validating",
          `Уточняем ${issues.length} полей на слайде ${i + 1} после проверки`,
          completed,
          p.contract.slides.length,
        );
      }
      if (!passed && shouldRebuild(issues, rejected)) {
        slides[i] = await reconstruct(spec, i, data, issues, rejected, signal);
        onSlideReady(i, slides[i]);
        await checkpoint(
          p,
          "filling",
          `Проверено слайдов: ${++completed}`,
          completed,
          p.contract.slides.length,
        );
        return;
      }
      if (!passed) {
        const saved = await readJson(path);
        await writeJson(path, {...saved, passed:true, issues:[], needsContentReview:true, acceptedWarnings:acceptCopyWarnings(data,issues,contentReviewVersion), qualityWarnings:qualityWarnings(issues,i+1)});
      }
      slides[i] = nativeSlide(
        data,
        layout,
        p.plan!.slides[i].title,
        i + 1,
        sourceOrdinal,
      );
      onSlideReady(i, slides[i]);
      await checkpoint(
        p,
        "filling",
        `Заполнено и проверено: ${++completed} из ${p.contract.slides.length}`,
        completed,
        p.contract.slides.length,
      );
      } catch (error) {
        signal.throwIfAborted();
        const result = await recoverSlideWithWarnings({output,assembled,index:i,layout:spec.native,
          title:p.plan!.slides[i].title,issues:[{reason:'repair_unavailable',code:(error as any)?.code}],signal,native});
        slides[i] = result.slide;
        await writeJson(join(output,`copy-${i}.json`),{data:result.data,passed:true,issues:[],needsContentReview:!result.slide.native?.preserveSource,rebuildNative:result.slide,rebuildNotice:result.notice});
        onSlideReady(i,slides[i]);
        await checkpoint(p,'filling',`Слайд ${i+1} сохранён с предупреждением`,++completed,p.contract.slides.length);
      }
    },
  );
  signal.throwIfAborted();
  // Geometry warnings are deliverable, but must not bypass the semantic check.
  // Review only unverified chosen copies, together, with one bounded text patch.
  await parallelSlides(slides,signal,async(slide,i,signal)=>{
    const path=join(output,`copy-${i}.json`);
    const saved=await readJson(path);
    if (!saved.needsContentReview || slide.native?.preserveSource) return;
    const spec=p.contract.slides[i];
    try {
      let layout=spec.native, semantics=spec.semantics;
      if (slide.native.rebuild) {
        const prepared=rebuildLayout(proposalFromRebuildAttempt({reason:saved.rebuildNotice?.reason || 'Проверка выбранного результата',scene:slide.native.rebuild,data:saved.data}),
          {...spec.native,rebuildSemantics:spec.semantics},(await currentRebuildProfiles())[spec.native.id],slide.native.rebuild.colorRepairs);
        layout=prepared.layout;semantics=prepared.semantics;
      }
      const result=await reviewFinalCopy({folder,output,index:i,topic:p.brief!.topic,source:projectFacts(p),approved:p.plan!.slides[i],
        slide,data:saved.data,layout,semantics,pool:evidencePool,signal,
        fit:async(candidate)=>(await native('fit',assembled,{slides:[candidate]},signal)).issues});
      slides[i]=result.slide;
      await writeJson(path,{...saved,data:result.data,needsContentReview:!result.reviewed,
        ...(saved.rebuildNative ? {rebuildNative:result.slide} : {}),
        qualityWarnings:[...(saved.qualityWarnings || []),...qualityWarnings(result.warnings,i+1)]});
    } catch(error) {
      signal.throwIfAborted();
      await writeJson(path,{...saved,qualityWarnings:[...(saved.qualityWarnings || []),{slide:i+1,reason:'content_review_unavailable',message:'Проверьте связь подписей и показателей: финальная проверка не завершилась.'}]});
    }
  });
  for (const [i, slide] of slides.entries()) {
    slide.native.ordinal = i + 1;
    slide.native.deckSlideCount = slides.length;
    slide.native.sourceSlideCount = p.analysis.layouts.length;
    slide.native.sourceOrdinal = p.analysis.layouts.find((layout: Layout) => layout.id === p.contract.slides[i].originalSourceLayoutId)?.index + 1;
  }
  await writeJson(join(folder, `template-r${p.revision}.json`), p.contract);
  await writeJson(join(folder, `filled-r${p.revision}.json`), {
    contractVersion: p.contract.version,
    assembledSha256: sha,
    research: p.brief?.webSearch ? p.research : undefined,
    slides,
  });
  return { slides, assembled, output };
}

export async function fillDeck(p: Project, signal: AbortSignal) {
  await researchProject(p, signal);
  delete p.result;
  await checkpoint(
    p,
    "filling",
    "Готовим тексты и изображения. Проверенные слайды используем повторно",
    0,
    p.contract.slides.length,
  );
  const output = join(projectDir(p.id), `output-r${p.revision}`);
  const identity = digest({
    version: 27,
    ...(engineContext()?.includeNotes ? {hostExportVersion:1,includeNotes:true} : {}),
    rebuildVersion, // Revalidate cached renders after recomposition changes.
    contextVersion,
    contentReviewVersion,
    revision: p.revision,
    sha: p.contract?.assembledSha256,
    brief: p.brief,
    plan: p.plan,
    facts: projectFacts(p),
  });
  const exportResult: any = await renderWithRepair({
    output,
    identity,
    slideCount: p.contract.slides.length,
    maxRepairs: 1,
    onUnresolved: async (result: any, issues) => {
      if (!result.pptxWritten) throw new HttpError(422,'Не удалось записать файл PPTX','pptx_not_written');
      return {...result,issues:[],warnings:[...(result.warnings || []),...issues]};
    },
    signal,
    prepare: async () => {
      let session: Awaited<ReturnType<typeof createVisualSession>> | undefined;
      const queued=new Map<number,any>();
      const sessionReady=(async()=>{
        await engineContext()?.descriptionsReady;
        await engineContext()?.prepareVisuals?.();
        session=await createVisualSession(p,signal);
        session.prewarm();
        for(const [i,slide] of queued)session.enqueue(i,slide);
        queued.clear();
        return session;
      })();
      void sessionReady.catch(()=>{});
      try {
        const prepared = await prepareDeck(p, signal, (i, slide) => {
          if (!slide.native?.preserveSource) {
            if(session)session.enqueue(i,slide);else queued.set(i,slide);
          }
          engineContext()?.onSlideReady?.(i, slide);
        });
        session=await sessionReady;
        if (
          p.visuals?.choices.some(
            (c) => c.mode === "generate" && c.status !== "ready",
          )
        )
          await checkpoint(
            p,
            "generating_images",
            "Тексты готовы. Завершаем параллельную генерацию крупных изображений",
          );
        const images = await session.finish();
        const filledPath = join(projectDir(p.id), `filled-r${p.revision}.json`);
        const filled = await readJson(filledPath);
        await engineContext()?.decorateSlides?.(prepared.slides);
        await writeJson(filledPath, { ...filled, slides: prepared.slides });
        return { ...prepared, images };
      } finally {
        await sessionReady.catch(()=>undefined);
        await session?.stop();
      }
    },
    render: async ({ slides, assembled, output, images }) => {
      await checkpoint(
        p,
        "validating",
        "Собираем итоговый PPTX с изображениями и проверяем текст и границы в PDF",
      );
      let result = await native(
        "export",
        assembled,
        { slides, output, images, allowQualityWarnings:true },
        signal,
      );
      const restored = restoreUnsafeImages(slides, result.issues || []);
      if (restored.length) {
        for (const item of restored) {
          const choice = p.visuals?.choices.find(
            (c) =>
              c.slideIndex === item.slideIndex && c.shapeId === item.shapeId,
          );
          if (choice) {
            choice.mode = "keep";
            choice.status = "failed";
            choice.applied = false;
            choice.message =
              "Новая картинка перекрывает содержимое. Сохранён оригинал; можно изменить настройки изображения.";
          }
        }
        const path = join(projectDir(p.id), `filled-r${p.revision}.json`);
        await writeJson(path, { ...(await readJson(path)), slides });
        await checkpoint(
          p,
          "validating",
          "Для конфликтующих изображений сохраняем оригиналы и повторно проверяем экспорт. Тексты не меняем",
        );
        result = await native(
          "export",
          assembled,
          { slides, output, images, allowQualityWarnings:true },
          signal,
        );
      }
      if(result.pdfAvailable!==false)await engineContext()?.onRendered?.(slides,output);
      return renderAdvisories(result);
    },
    notify: async (issues, round) => {
      const ordinals = [...new Set(issues.map((e) => e.slide! + 1))].join(", ");
      await checkpoint(
        p,
        "validating",
        `Автоматически исправляем результат рендеринга: слайды ${ordinals}, проход ${round}`,
      );
    },
  });
  const filledPath = join(projectDir(p.id), `filled-r${p.revision}.json`);
  const filled = await readJson(filledPath);
  for (const choice of p.visuals?.choices || []) {
    choice.applied = !!filled.slides[choice.slideIndex]?.images?.some(
      (image: any) =>
        image.shapeId === choice.shapeId && image.image === choice.asset,
    );
  }
  await writeJson(filledPath, filled);
  if (exportResult.pdfAvailable === false) {
    await Promise.all(["presentation.pdf",...p.contract.slides.map((_:unknown,i:number)=>`slide-${i}.png`)].map(name=>rm(join(output,name),{force:true})));
  }
  const fieldChanges = await readJson(join(output, "field-changes.json"));
  const copies = await Promise.all(p.contract.slides.map((_:unknown,i:number)=>readJson(join(output,`copy-${i}.json`))));
  const warnings = uniqueWarnings([
    ...(p.researchWarning ? [{reason:'research_incomplete',message:p.researchWarning}] : []),
    ...(p.planWarnings || []),
    ...(p.planAdaptation?.warnings || []),
    ...copies.flatMap((copy:any,i:number)=>[...(copy.qualityWarnings || []),...qualityWarnings(copy.citationWarnings || [],i+1),...qualityWarnings(copy.rebuildNotice?.warnings || [],i+1)]),
    ...qualityWarnings(exportResult.warnings || []),
    ...(exportResult.renderQuality?.pages || []).flatMap((page:any,i:number)=>qualityWarnings(page.warnings || [],i+1)),
  ]);
  await writeJson(join(output,'quality-warnings.json'),{warnings});
  p.result = {
    warnings,
    pdfAvailable: exportResult.pdfAvailable !== false,
    slides: p.contract.slides.length,
    expandedFields: fieldChanges.changes.length,
    rebuiltSlides: (
      await Promise.all(
        p.contract.slides.map((_: unknown, i: number) =>
          readJson(join(output, `copy-${i}.json`)),
        ),
      )
    ).flatMap((copy) => (copy.rebuildNotice ? [copy.rebuildNotice] : [])),
  };
  await checkpoint(
    p,
    "complete",
    warnings.length ? `Презентация готова с замечаниями (${warnings.length}). Проверьте отмеченные слайды; PPTX доступен для скачивания.` : p.visuals?.choices.some(
      (c) => (c.mode !== "keep" || c.status === "failed") && !c.applied,
    )
      ? "Презентация готова. Для части изображений сохранён оригинал: причины и повтор доступны в блоке изображений."
      : "Презентация готова. Проверьте содержание и скачайте PPTX или PDF",
    p.result.slides,
    p.result.slides,
  );
}
