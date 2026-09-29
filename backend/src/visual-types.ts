import { z } from "zod";
export const visualKinds = [
  "illustration",
  "photo",
  "diagram",
  "chart",
  "interface",
] as const;
export const visualDescriptionSchema = z.object({
  slotIndex: z.number().int().min(0),
  role: z.enum(["content", "photo_underlay", "brand", "decoration", "icon"]),
  kind: z.enum(visualKinds),
  description: z.string().trim().min(1).max(700),
  style: z.string().trim().max(500),
  fieldKeys: z.array(z.string()).max(5),
  background: z.enum(["transparent", "opaque"]),
});
export type VisualDescription = z.infer<typeof visualDescriptionSchema>;
export type VisualSlot = {
  shapeId: number;
  kind: "picture" | "placeholder";
  box: { x: number; y: number; w: number; h: number };
  aspectRatio: number;
  sourceRole: string;
  protected: boolean;
  requiresOpaque: boolean;
  name?: string;
  detection?: {
    method: "explicit_image_instruction";
    markerFieldKeys: string[];
    markerShapeIds: number[];
    labels: string[];
  };
};
export type VisualChoice = VisualDescription & {
  slideIndex: number;
  shapeId: number;
  slot: VisualSlot;
  mode: "keep" | "generate" | "upload";
  instruction: string;
  quality: "low" | "medium" | "high";
  nonce: number;
  asset?: string;
  applied?: boolean;
  status?: "ready" | "failed" | "uncertain" | "generating";
  message?: string;
  generationHash?: string;
  preserveReason?: string;
};
export type VisualPlan = {
  version: 1;
  detectionVersion?: number;
  revision: number;
  assembledSha256: string;
  choices: VisualChoice[];
};
export const visualAnalysisPrompt = `Дополнительно верни images: по одному элементу для КАЖДОГО visualSlots: {slotIndex,role,kind,description,style,fieldKeys,background}.
Если detection.method=explicit_image_instruction, структура PPTX уже подтвердила отдельную рамку с инструкцией «вставить фото/изображение». Это role=content: предложи тематическое изображение, а не декоративную плашку. markerFieldKeys — служебные надписи, их нельзя использовать как содержательную подпись. Для таких рамок AI выбирает сюжет и kind, но не меняет границы.
slotIndex — индекс из массива, shapeId и box задают точное место на изображении слайда. Иконки, пиктограммы и маленькие служебные знаки всегда role=icon: сохраняй оригинал, не придумывай им новый сюжет и не заменяй иллюстрацией. Тип большого изображения (photo, illustration, diagram, chart, interface) определяй самостоятельно по оригиналу, теме и тексту; пользователь не должен выбирать его вручную. role: content для заменяемого тематического изображения, photo_underlay для фотографии под текстом, brand для логотипа/фирменного знака, decoration для рамки/узора/декора. Нельзя определять фон только по размеру: полноэкранная тематическая фотография может быть photo_underlay. Не путай иллюстрацию со служебным декором. Сначала классифицируй роль по ОРИГИНАЛУ, без переосмысления под новую тему. Любой фирменный знак/эмблема/логотип, даже не связанный с новой темой, — brand. Плашки показателей, цветные стрелки под отдельными текстовыми полями, рамки, узоры и фоновые декоративные фигуры — decoration: их нельзя заменять тематическими картинками. Сохраняй логотипы, орнаменты и рамки устройств. kind: illustration, photo, diagram (связи/процесс), chart (диаграмма по подтверждённым данным) или interface. description — сначала точно опиши видимый оригинал; для content/photo_underlay добавь новую визуальную историю по утверждённому плану. Для brand/decoration только описание оригинала, не предлагай новый знак или новый декор. style — характер рисунка/фотографии, материалы, освещение, палитра, композиция (без старых фактов). fieldKeys — ближайшие смысловые поля, которые поясняет изображение. background transparent по умолчанию для изолированного предмета, включая фотографический объект, иллюстрацию, схему или макет устройства. Ориентир 80–90% подходящих мест без фона, не механическая квота. opaque только если целостная сцена/окружение существенны для смысла либо это подложка текста. Подбирай цвета и характер рисунка по стилю презентации. При requiresOpaque всегда opaque. Для protected всегда brand и без генерации. Не превращай редактируемые таблицы, диаграммы или текст в картинку. Изображённый интерфейс — концепт, не подтверждение существования продукта.`;
export function validateVisualDescriptions(
  raw: unknown,
  slots: VisualSlot[],
  keys: string[],
) {
  const descriptions = z.array(visualDescriptionSchema).parse(raw ?? []);
  if (
    new Set(descriptions.map((v) => v.slotIndex)).size !==
      descriptions.length ||
    descriptions.some(
      (v) => !slots[v.slotIndex] || v.fieldKeys.some((k) => !keys.includes(k)),
    )
  )
    throw new Error("visual_slot_mapping");
  return slots.map((slot, slotIndex) => {
    if (slot.sourceRole === "icon" || slot.protected)
      return {
        slotIndex,
        role:
          slot.sourceRole === "icon" ? ("icon" as const) : ("brand" as const),
        kind: "illustration" as const,
        description:
          slot.sourceRole === "icon"
            ? "Исходная иконка шаблона"
            : "Исходный логотип или служебный элемент",
        style: "",
        fieldKeys: [],
        background: slot.requiresOpaque
          ? ("opaque" as const)
          : ("transparent" as const),
      };
    const v = descriptions.find((v) => v.slotIndex === slotIndex);
    if (!v) throw new Error("visual_slot_mapping");
    return {
      ...v,
      ...(slot.detection?.method === "explicit_image_instruction"
        ? { role: "content" as const }
        : {}),
      background:
        slot.requiresOpaque
          ? ("opaque" as const)
          : v.background,
    };
  });
}
/** Native table resizing can remove text cells after an image description was
 * cached. Keep the visual choice, but detach links to cells that no longer
 * exist in the assembled slide. Slot identity is still validated strictly. */
export function reconcileVisualDescriptions(
  raw: unknown,
  slots: VisualSlot[],
  keys: string[],
) {
  const available = new Set(keys);
  const current = Array.isArray(raw)
    ? raw.map((value) =>
        value && typeof value === "object" && Array.isArray(value.fieldKeys)
          ? { ...value, fieldKeys: value.fieldKeys.filter((key: unknown) => available.has(key as string)) }
          : value,
      )
    : raw;
  return validateVisualDescriptions(current, slots, keys);
}
export function modelVisualSlots(slots: VisualSlot[]) {
  return slots
    .map((slot, slotIndex) => ({ ...slot, slotIndex }))
    .filter((slot) => !slot.protected && slot.sourceRole !== "icon");
}

/** Keep small native marks even when the visual model calls them content.
 * Ratios are slide-relative and apply to all templates, never shape IDs. */
export function visualPreserveReason(
  choice: Pick<VisualChoice, "slot" | "role">,
  dimensions: { width: number; height: number },
) {
  const { slot, role } = choice;
  if (slot.sourceRole === "icon" || role === "icon")
    return "Иконка шаблона: сохраняется без генерации";
  if (slot.protected || role === "brand")
    return "Логотип или служебный элемент: сохраняется";
  if (role === "decoration") return "Декоративный элемент: сохраняется";
  const b = slot.box;
  const w = Math.max(
    0,
    Math.min(dimensions.width, b.x + b.w) - Math.max(0, b.x),
  );
  const h = Math.max(
    0,
    Math.min(dimensions.height, b.y + b.h) - Math.max(0, b.y),
  );
  if (
    !dimensions.width ||
    !dimensions.height ||
    (w * h) / (dimensions.width * dimensions.height) < 0.012
  )
    return "Маленький элемент шаблона: сохраняется без генерации";
  return undefined;
}
