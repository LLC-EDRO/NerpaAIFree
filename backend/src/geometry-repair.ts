import { join } from "node:path";
import { z } from "zod";
import { llmJson } from "./llm.js";
import { readJson, writeJson } from "./store.js";
import { digest, renderedRepairImage, type Issue } from "./repair.js";
import { nativeSlide, type Layout } from "./domain.js";

const proposalSchema = z
  .object({
    cause: z.string().max(1200),
    action: z.string().max(1200),
    frames: z
      .array(
        z
          .object({
            key: z.string(),
            x: z.number().finite(),
            y: z.number().finite(),
            w: z.number().positive().finite(),
            h: z.number().positive().finite(),
          })
          .strict(),
      )
      .max(12),
    fields: z
      .array(
        z
          .object({
            key: z.string(),
            text: z.string(),
            evidence: z.array(z.string()),
          })
          .strict(),
      )
      .max(40)
      .default([]),
  })
  .strict();
export function parseGeometryProposal(
  raw: unknown,
  allowed: Set<string>,
  textAllowed = new Set<string>(),
) {
  const proposal = proposalSchema.parse(raw);
  if (
    new Set(proposal.frames.map((f) => f.key)).size !==
      proposal.frames.length ||
    proposal.frames.some((f) => !allowed.has(f.key))
  )
    throw new Error("Unknown or duplicate geometry field");
  if (
    new Set(proposal.fields.map((f) => f.key)).size !==
      proposal.fields.length ||
    proposal.fields.some((f) => !textAllowed.has(f.key))
  )
    throw new Error("Unknown or duplicate text field");
  return proposal;
}

export function geometryAttemptAvailable(attempts: { accepted: boolean }[]) {
  // A partially successful proposal deserves feedback on its remaining fields.
  // Two consecutive refusals stop the strategy; four calls is the hard ceiling.
  return (
    attempts.length < 4 &&
    !(attempts.length >= 2 && attempts.slice(-2).every((a) => !a.accepted))
  );
}

export function geometryBounds(
  fields: any[],
  current: Record<string, any> = {},
  issues: Issue[] = [],
) {
  return fields.map((field) => {
    const original = field.box,
      frame = current[field.key] || original;
    const fit = issues.find(
      (i) =>
        i.key === field.key && typeof (i as any).measuredHeightPt === "number",
    ) as any;
    return {
      key: field.key,
      current: frame,
      originalBounds: {
        left: original.x,
        top: original.y,
        right: original.x + original.w,
        bottom: original.y + original.h,
      },
      ...(fit
        ? {
            suggestedHeightPt:
              frame.h +
              Math.max(0, fit.measuredHeightPt - fit.availableHeightPt) +
              1.5,
          }
        : {}),
    };
  });
}

/** A long paragraph needs a concise rewrite first, not repeated attempts to
 * stretch a card across neighbours. Tiny line-height deficits still go to AI. */
export function geometryRepairKeys(issues: Issue[], layout: Layout) {
  return new Set(
    issues
      .filter((i: any) => {
        if (
          !i.key ||
          i.reason !== "overflow" ||
          !i.details?.some((d: string) =>
            [
              "source_text_frame_overflow",
              "rendered_text_overlap",
              "rendered_text_overlaps_reserved_object",
              "rendered_text_overlaps_source_text",
              "rendered_text_outside_frame",
              "source_text_reserved_region",
            ].includes(d),
          )
        )
          return false;
        const slot = layout.slots.find((s) => s.key === i.key);
        const measured = i.measuredHeightPt,
          available = i.availableHeightPt;
        if (
          slot?.role === "body" &&
          Number.isFinite(measured) &&
          available > 0 &&
          measured > available * 1.4 &&
          i.suggestedMaxChars > 0
        )
          return false;
        return true;
      })
      .map((i) => i.key!),
  );
}

/** Stop buying text rewrites when local text and geometry attempts are both exhausted. */
export async function geometryRepairExhausted(
  output: string,
  index: number,
  layout: Layout,
) {
  try {
    const saved = await readJson(join(output, `geometry-repair-${index}.json`));
    return (
      saved.identity ===
        digest({
          version: 4,
          layout: { ...layout, geometryRepairs: undefined },
        }) && !geometryAttemptAvailable(saved.attempts || [])
    );
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw e;
  }
}

/** The model advises; the native engine measures and enforces every boundary.
 * Durable attempts prevent paying again for the same failed geometry strategy. */
export async function repairGeometry(input: {
  folder: string;
  output: string;
  assembled: string;
  index: number;
  title: string;
  layout: Layout;
  data: any;
  issues: Issue[];
  signal: AbortSignal;
  native: (
    action: string,
    folder: string,
    extra: any,
    signal?: AbortSignal,
  ) => Promise<any>;
  textRepair?: {
    keys: string[];
    context: unknown;
    validate: (data: any) => { data: any; issues: any[] };
  };
  model?: typeof llmJson;
}) {
  const keys = geometryRepairKeys(input.issues, input.layout);
  if (!keys.size) return undefined;
  const statePath = join(input.output, `geometry-repair-${input.index}.json`);
  const identity = digest({
    version: 4,
    layout: { ...input.layout, geometryRepairs: undefined },
  });
  let state: any = { identity, attempts: [] };
  try {
    const saved = await readJson(statePath);
    if (saved.identity === identity) state = saved;
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
  }
  if (!geometryAttemptAvailable(state.attempts)) return undefined;
  const slide = (data = input.data) =>
    nativeSlide(data, input.layout, input.title, input.index + 1);
  const initial = await input.native(
    "fit",
    input.assembled,
    { slides: [slide()], repairOptions: [...keys] },
    input.signal,
  );
  const renderedIssue = input.issues.some((i) =>
    i.details?.some((d) => d.startsWith("rendered_")),
  );
  if (!initial.issues.length && !renderedIssue)
    return {
      issues: [],
      diagnosis: {
        cause: "Сохранённая ошибка не воспроизводится текущим измерением",
        action: "Сохранённый текст принят после повторной проверки",
        fields: [...keys],
      },
    };
  const context = initial.geometryOptions?.[0];
  const allowed = new Set<string>(
    (context?.fields || [])
      .filter((f: any) => keys.has(f.key))
      .map((f: any) => f.key),
  );
  const textAllowed = new Set(input.textRepair?.keys || []);
  if (!allowed.size && !textAllowed.size) return undefined;
  // Native text fit cannot prove that an overlap seen in the PDF disappeared.
  // For editable frames, fix the geometry instead of accepting another string
  // merely because it passes the same already-successful native measurement.
  const requiresFrameRepair = allowed.size > 0 && input.issues.some(i =>
    i.details?.some(d => /^rendered_text_overlap/.test(d)));
  const before = input.layout.geometryRepairs;
  const failedImage = await renderedRepairImage(
    input.output,
    input.index,
    input.data,
  );
  for (; geometryAttemptAvailable(state.attempts);) {
    const attempt = state.attempts.length;
    const proposal = await (input.model || llmJson)({
      stage: `fill-geometry-${input.index + 1}-${attempt}`,
      folder: input.folder,
      signal: input.signal,
      images: [
        join(input.assembled, "previews", `slide-${input.index}.png`),
        ...(failedImage ? [failedImage] : []),
      ],
      prompt:
        "Проанализируй исходный слайд, текущий текст и измеренную ошибку. Выбери минимальное исправление: расширение, сужение или локальное перемещение прозрачной рамки в свободную область. При пересечении с картинкой, графиком, легендой или соседним текстом исправь геометрию: сокращение само по себе может не помочь. Верни {cause,action,frames:[{key,x,y,w,h}]} в pt. Меняй только allowedKeys. Новая рамка не обязана содержать прежнюю. Сохраняй область композиции, уменьшай существующие пересечения, не создавай новые и не выходи за страницу. При нехватке высоты одной строки сокращение слов НЕ помогает: предложи небольшое увеличение высоты с запасом для реального рендера. Для центрального выравнивания по вертикали можно симметрично расширить вверх и вниз, если есть место. Не меняй шрифт. Если передан allowedTextKeys, можно одновременно вернуть fields:[{key,text,evidence:[ID факта]}] с кратким перефразированием, сохранив числа и смысл. Для неподтверждённой цифры выбери подтверждённый факт из textRepair. Можно исправить только текст (frames:[]) либо текст и рамку вместе; выбирай минимальный патч. Если текст менять не нужно, fields:[]. Не предлагай огромную область вместо минимального исправления. При отсутствии безопасного места frames:[]. Отказ сервера и прошлые предложения учитывай, не повторяй их.",
      payload: {
        slide: input.index + 1,
        title: input.title,
        imageRoles: failedImage
          ? ["source_layout", "failed_render_of_current_text"]
          : ["source_layout"],
        allowedKeys: [...allowed],
        requiresFrameRepair,
        geometryRequirement: requiresFrameRepair
          ? "Пересечение подтверждено в PDF. Обязательно измени размер или положение хотя бы одной доступной рамки, чтобы устранить пересечение; сокращение текста без frames не принимается как геометрическое исправление."
          : undefined,
        allowedTextKeys: [...textAllowed],
        textRepair: input.textRepair?.context,
        issues: input.issues,
        geometry: context,
        coordinateConstraints: geometryBounds(
          context?.fields || [],
          before,
          initial.issues,
        ),
        coordinateInstructions:
          "originalBounds — исходная область, не обязательный минимум. container в geometry.fields — границы цветного блока: оставайся внутри него; координаты всех полей заданы относительно слайда, включая вложенные группы. Если новая рамка совсем не пересекает исходную, смещение x не более 36 pt, y не более max(72, исходная высота). suggestedHeightPt — подсказка по измерению, а не разрешение пересечь соседей. Проверяй все четыре границы численно.",
        currentGeometry: before || {},
        text: Object.fromEntries(
          Object.entries(input.data.fields).map(([k, v]: [string, any]) => [
            k,
            v.text,
          ]),
        ),
        previousAttempts: state.attempts,
      },
      validate: (v) => {
        const parsed = parseGeometryProposal(v, allowed, textAllowed);
        if (requiresFrameRepair && !parsed.frames.some(f => {
          const original = before?.[f.key] || context.fields.find((x: any) => x.key === f.key)?.box;
          return original && ["x","y","w","h"].some(k => Math.abs((f as any)[k]-original[k]) > .1);
        })) throw new Error("Rendered overlap requires a changed frame, not only rewritten text");
        return parsed;
      },
    });
    if (!proposal.frames.length && !proposal.fields.length) {
      state.attempts.push({ proposal, accepted: false });
      await writeJson(statePath, state);
      return undefined;
    }
    let candidate = structuredClone(input.data);
    for (const { key, text, evidence } of proposal.fields)
      candidate.fields[key] = { ...candidate.fields[key], text, evidence };
    if (proposal.fields.length) {
      const checked = input.textRepair!.validate(candidate);
      if (checked.issues.length) {
        state.attempts.push({
          proposal,
          accepted: false,
          issues: checked.issues,
        });
        await writeJson(statePath, state);
        continue;
      }
      candidate = checked.data;
    }
    input.layout.geometryRepairs = {
      ...before,
      ...Object.fromEntries(
        proposal.frames.map(({ key, ...box }) => [key, box]),
      ),
    };
    let fit: any;
    try {
      fit = await input.native(
        "fit",
        input.assembled,
        { slides: [slide(candidate)] },
        input.signal,
      );
    } finally {
      input.layout.geometryRepairs = before;
    }
    const signature = (i: Issue) =>
      `${i.key}:${i.reason}:${i.details?.join(",")}`;
    const existing = new Set(initial.issues.map(signature));
    const accepted =
      (renderedIssue
        ? !fit.issues.length
        : fit.issues.length < initial.issues.length) &&
      fit.issues.every((i: Issue) => existing.has(signature(i)));
    state.attempts.push({ proposal, accepted, issues: fit.issues });
    await writeJson(statePath, state);
    if (accepted) {
      input.layout.geometryRepairs = {
        ...before,
        ...Object.fromEntries(
          proposal.frames.map(({ key, ...box }) => [key, box]),
        ),
      };
      return {
        data: candidate,
        issues: fit.issues,
        diagnosis: {
          cause: proposal.cause,
          action: proposal.action,
          fields: [
            ...new Set(
              [...proposal.frames, ...proposal.fields].map((f) => f.key),
            ),
          ],
        },
      };
    }
  }
  return undefined;
}
