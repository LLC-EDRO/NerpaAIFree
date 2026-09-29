import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { readJson, writeJson } from "./store.js";
import { requiresLayoutRepair } from "./layout-conflicts.js";
import { HttpError } from "./errors.js";

export type Issue = {
  slide?: number;
  key?: string;
  reason: string;
  details?: string[];
  suggestedMaxChars?: number;
  message?: string;
  [key: string]: unknown;
};
/** A PDF may rename/subset a successfully rendered fallback font. Rewriting copy
 * cannot fix a font name. Missing glyphs, clipping and mixed issues still repair. */
export function renderAdvisories<T extends { issues?: Issue[]; warnings?: Issue[] }>(result: T): T {
  const advisory = (result.issues || []).filter(issue => issue.reason === "unverifiable" &&
    issue.details?.length && issue.details.every(detail => detail === "rendered_font_mismatch"));
  if (!advisory.length) return result;
  return {...result, issues: result.issues!.filter(issue => !advisory.includes(issue)),
    warnings: [...(result.warnings || []), ...advisory]};
}

export const digest = (value: unknown) =>
  createHash("sha256").update(JSON.stringify(value)).digest("hex");
const imageDigest = (value: Buffer) =>
  createHash("sha256").update(value).digest("hex");

/** Only show the exact failed render, never an image from an earlier draft. */
export async function renderedRepairImage(
  output: string,
  slide: number,
  data: unknown,
) {
  try {
    const state = await readJson(join(output, "repair-state.json"));
    const pending = state.pending?.find(
      (p: any) => p.slide === slide && p.hash === digest(data),
    );
    const name = `slide-${slide}.png`;
    if (!pending?.preview || pending.preview.name !== name) return undefined;
    const path = join(output, name);
    return imageDigest(await readFile(path)) === pending.preview.sha256
      ? path
      : undefined;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

export function repairAnalysis(raw: any, keys: string[]) {
  const value = raw?.repairAnalysis;
  if (
    !value ||
    typeof value.cause !== "string" ||
    typeof value.action !== "string"
  )
    return undefined;
  return {
    cause: value.cause.slice(0, 1200),
    action: value.action.slice(0, 1200),
    fields: Array.isArray(value.fields)
      ? [
          ...new Set<string>(
            value.fields.filter(
              (k: any) => typeof k === "string" && keys.includes(k),
            ),
          ),
        ]
      : [],
  };
}
// Match runtime.text_frames.FIT_VERSION. Renderer failures are not estimates
// and must survive fitter upgrades; obsolete estimates must not ban valid copy.
export const nativeFitVersion = 7;
export function currentFitHistory(issues: Issue[]): Issue[] {
  return issues.filter(
    (issue) =>
      !issue.geometrySuperseded &&
      // The old placeholder check rejected partial frame intersections even
      // when no glyph was hidden. Re-measure these with current native geometry.
      !(
        issue.details?.length &&
        issue.details.every(
          (detail) => detail === "source_picture_placeholder_occludes_text",
        ) &&
        issue.geometryVersion !== 2
      ) &&
      !(
        issue.reason === "overflow" &&
        issue.details?.length &&
        issue.details.every((detail) =>
          ["source_text_frame_overflow", "source_text_outside_page", "source_text_reserved_region"].includes(
            detail,
          ),
        ) &&
        issue.fitVersion !== nativeFitVersion
      ),
  );
}
export function rejections(data: any, issues: Issue[], stage: string) {
  return issues
    .filter((e) => e.key)
    .map((e) => ({
      ...e,
      stage,
      value: data.fields[e.key!]?.text ?? data.charts[e.key!],
      contextHash: digest(data),
    }));
}
/** Geometry depends on text, not its citation. Evidence repairs may keep text. */
export function repeatedGeometryIssues(data: any, rejected: any[]): Issue[] {
  const found = new Map<string, Issue>();
  for (const old of currentFitHistory(rejected)) {
    if (
      !old.key ||
      old.reason !== "overflow" ||
      old.value !== data.fields[old.key]?.text
    )
      continue;
    if (
      old.details?.includes("rendered_text_overlap") &&
      old.contextHash !== digest(data)
    )
      continue;
    found.set(old.key, {
      ...old,
      message:
        old.message ||
        "Этот текст уже не поместился. Исправь причину overflowAxes: при нехватке высоты уменьши число абзацев, при нехватке ширины сократи строки, сохранив факты и смысл. Повторное принятие того же текста запрещено.",
    });
  }
  return [...found.values()];
}

/** Repair responses may be patches. Unaffected values never come from the
 * repair model, even when it returns a whole replacement slide. */
export function mergeRepairCandidate(raw: any, previous: any, issues: Issue[]) {
  if (!previous || !issues.length || !raw || typeof raw !== "object")
    return raw;
  const result = structuredClone(previous);
  const keys = new Set(issues.map((e) => e.key));
  for (const section of ["fields", "charts"] as const) {
    if (
      raw[section] !== undefined &&
      (!raw[section] ||
        typeof raw[section] !== "object" ||
        Array.isArray(raw[section]))
    )
      throw new Error(`Invalid repair ${section}`);
    for (const [key, value] of Object.entries(raw[section] || {})) {
      if (!(key in previous[section]))
        throw new Error(`Unknown repair key ${key}`);
      if (keys.has(key)) result[section][key] = value;
    }
  }
  return result;
}

/** Durable feedback from the final renderer. A crash after the report is saved
 * cannot leave the failed text permanently accepted by the copy cache. */
export async function renderWithRepair<T extends { issues?: Issue[] }>(input: {
  output: string;
  identity: string;
  slideCount: number;
  signal: AbortSignal;
  prepare: () => Promise<unknown>;
  render: (prepared: any) => Promise<T>;
  notify: (issues: Issue[], round: number) => Promise<void>;
  maxRepairs?: number;
  onUnresolved?: (result: T, issues: Issue[]) => Promise<T>;
}): Promise<T> {
  const path = join(input.output, "repair-state.json");
  let state: any = {
    identity: input.identity,
    rounds: 0,
    pending: [],
    history: [],
  };
  try {
    const saved = await readJson(path);
    if (saved.identity === input.identity) state = saved;
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
  }
  if (input.onUnresolved) delete state.terminal;
  let lastResult: T | undefined;
  const finishWithWarnings = async (result: T, issues: Issue[]) => {
    const accepted = await input.onUnresolved!(result, issues);
    input.signal.throwIfAborted();
    state.complete = true;
    state.warnings = issues;
    state.pending = [];
    delete state.terminal;
    await writeJson(path, state);
    return accepted;
  };
  const stop = (issues: Issue[], code: string) =>
    new HttpError(
      422,
      "Автоматическое исправление не смогло получить проверенный результат. Причины и варианты сохранены в диагностике; готовый файл не опубликован.",
      code,
      { issues, repairRounds: state.rounds },
    );
  while (true) {
    input.signal.throwIfAborted();
    if (state.terminal)
      throw stop(state.history.at(-1)?.issues || [], state.terminal);
    // Replay only against the exact failed content, never a later edited copy.
    for (const pending of state.pending) {
      const copyPath = join(input.output, `copy-${pending.slide}.json`);
      const copy = await readJson(copyPath);
      if (digest(copy.data) !== pending.hash) continue;
      const rejected = [
        ...(copy.rejected || []),
        ...rejections(copy.data, pending.issues, "render"),
      ];
      await writeJson(copyPath, {
        ...copy,
        passed: false,
        acceptedData:
          copy.acceptedData || (copy.passed ? copy.data : undefined),
        issues: pending.issues,
        rebuildRequested:
          requiresLayoutRepair(pending.issues) || state.history.filter((h: any) =>
            h.issues.some(
              (issue: Issue) =>
                issue.slide === pending.slide &&
                ["overflow", "unverifiable"].includes(issue.reason),
            ),
          ).length >= 2,
        rejected: [...new Map(rejected.map((e) => [digest(e), e])).values()],
      });
    }
    if (!input.onUnresolved && state.pending.length && state.rounds > (input.maxRepairs ?? 3))
      throw stop(
        state.pending.flatMap((p: any) => p.issues),
        "automatic_repair_exhausted",
      );
    const prepared = await input.prepare();
    input.signal.throwIfAborted();
    const payloadHash = digest(prepared);
    if (
      !input.onUnresolved && state.history.some(
        (h: any) => h.payloadHash === payloadHash && h.issues.length,
      )
    )
      throw stop(
        state.history.find((h: any) => h.payloadHash === payloadHash).issues,
        "automatic_repair_no_progress",
      );
    const repeated = state.history.some((h: any) => h.payloadHash === payloadHash && h.issues.length);
    if (repeated && input.onUnresolved && lastResult)
      return finishWithWarnings(lastResult, lastResult.issues || []);
    const result = await input.render(prepared);
    lastResult = result;
    input.signal.throwIfAborted();
    const issues = result.issues || [];
    state.history.push({ at: new Date().toISOString(), payloadHash, issues });
    state.pending = [];
    if (!issues.length) {
      state.complete = true;
      await writeJson(path, state);
      return result;
    }
    state.complete = false;
    if (input.onUnresolved && (repeated || state.rounds >= (input.maxRepairs ?? 3)))
      return finishWithWarnings(result, issues);
    let repairable = true;
    for (const slide of new Set(issues.map((e) => e.slide))) {
      if (
        !Number.isInteger(slide) ||
        slide! < 0 ||
        slide! >= input.slideCount
      ) {
        repairable = false;
        continue;
      }
      const copy = await readJson(join(input.output, `copy-${slide}.json`));
      const selected = issues.filter((e) => e.slide === slide);
      if (
        selected.some(
          (e) =>
            !e.key || !(e.key in copy.data.fields || e.key in copy.data.charts),
        )
      )
        repairable = false;
      let preview;
      const name = `slide-${slide}.png`;
      try {
        // A fit/export failure can precede rendering and leave an old PNG.
        if ("renderQuality" in result && result.renderQuality)
          preview = {
            name,
            sha256: imageDigest(await readFile(join(input.output, name))),
          };
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
      state.pending.push({
        slide,
        hash: digest(copy.data),
        issues: selected,
        ...(preview ? { preview } : {}),
      });
    }
    state.rounds++;
    // Journal first, then invalidate checkpoints at the top of the loop.
    if (!repairable) state.terminal = "automatic_repair_unaddressable";
    await writeJson(path, state);
    if (!repairable && input.onUnresolved) return finishWithWarnings(result, issues);
    if (!repairable) throw stop(issues, state.terminal);
    if (state.rounds <= (input.maxRepairs ?? 3))
      await input.notify(issues, state.rounds);
  }
}

/** A new metric needs its unit and label updated together; unrelated groups
 * remain frozen by mergeRepairCandidate. */
export function metricRepairIssues(
  issues: Issue[],
  fields: { key: string; role: string; group: string; action: string }[],
) {
  const metricGroups = new Set(
    fields.filter((f) => f.role === "metric").map((f) => f.group),
  );
  const groups = new Set(
    fields
      .filter(
        (f) => metricGroups.has(f.group) && issues.some((e) => e.key === f.key),
      )
      .map((f) => f.group),
  );
  return [
    ...issues,
    ...fields
      .filter(
        (f) =>
          groups.has(f.group) &&
          f.action === "replace" &&
          !issues.some((e) => e.key === f.key),
      )
      .map((f) => ({
        key: f.key,
        reason: "metric_context_changed",
        message:
          "Согласуй число, подпись и единицы в этой группе. Если точная подпись не помещается, можно выбрать другой подтверждённый факт по теме с более короткой подписью. Нельзя сокращать подпись до потери предмета или периода. Если факт не изменился, сохрани прежнее число.",
      })),
  ];
}

/** Escalate repeated local conflicts to one coherent slide proposal. Branding
 * remains locked; native checks and content review still validate the result. */
export function holisticRepairIssues(
  issues: Issue[],
  fields: { key: string; action: string }[],
  rejected: Issue[],
) {
  const substantive = (i: Issue) =>
    ["overflow", "content_mismatch", "metric_fact_required"].includes(i.reason);
  if (!issues.some(substantive) || rejected.filter(substantive).length < 3)
    return issues;
  return [
    ...issues,
    ...fields
      .filter(
        (f) => f.action === "replace" && !issues.some((i) => i.key === f.key),
      )
      .map((f) => ({
        key: f.key,
        reason: "slide_context_changed",
        message:
          "Локальные исправления конфликтуют. Согласуй слайд целиком: разные карточки не должны дублировать один показатель. Сохрани единицы и понятный субъект, перенеси короткие единицы в подписи при нехватке места. Можно менять связанные тексты всех разрешённых блоков, но не геометрию и оформление.",
      })),
  ];
}

/** After local edits fail, let the model redistribute meaning within the
 * template's existing semantic group. Never unlock branding or other groups. */
export function semanticRepairIssues(
  issues: Issue[],
  fields: { key: string; role: string; group: string; action: string }[],
  rejected: Issue[],
) {
  const addressable = issues.filter(
    (i) =>
      i.key &&
      ![
        "evidence_invalid",
        "metric_context_changed",
        "semantic_context_changed",
      ].includes(i.reason),
  );
  const groups = new Set(
    fields
      .filter(
        (f) =>
          f.action === "replace" &&
          f.group?.trim() &&
          addressable.some((i) => i.key === f.key) &&
          rejected.filter(
            (r) =>
              r.key === f.key &&
              r.reason !== "evidence_invalid" &&
              r.reason !== "semantic_context_changed",
          ).length >= 2,
      )
      .map((f) => f.group),
  );
  return [
    ...issues,
    ...fields
      .filter(
        (f) =>
          f.action === "replace" &&
          groups.has(f.group) &&
          !issues.some((i) => i.key === f.key),
      )
      .map((f) => ({
        key: f.key,
        reason: "semantic_context_changed",
        message:
          "Точечные правки связанного поля не помогли. Перераспредели смысл внутри этой группы по доступным полям, сохрани факты, единицы и читаемость. Если содержание помещается, оставь его. Не добавляй сведения ради заполнения и не меняй другие группы.",
      })),
  ];
}
