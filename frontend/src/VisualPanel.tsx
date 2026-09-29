import { useState } from "react";
export type VisualChoice = {
  slideIndex: number;
  slotIndex: number;
  shapeId: number;
  slot: {
    box: { x: number; y: number; w: number; h: number };
    protected: boolean;
    requiresOpaque: boolean;
    detection?: { method: string; labels: string[] };
  };
  mode: "keep" | "generate" | "upload";
  kind: "illustration" | "photo" | "diagram" | "interface";
  background: "transparent" | "opaque";
  instruction: string;
  quality: "low" | "medium" | "high";
  description: string;
  style: string;
  asset?: string;
  applied?: boolean;
  nonce: number;
  preserveReason?: string;
  status?: string;
  message?: string;
};
export type VisualPlan = { revision: number; choices: VisualChoice[] };
const names = {
  illustration: "Иллюстрация",
  photo: "Фотография",
  diagram: "Схема",
  interface: "Концепт интерфейса",
};
function ChoiceEditor({
  choice,
  busy,
  save,
  upload,
}: {
  choice: VisualChoice;
  busy: boolean;
  save: (v: unknown) => void;
  upload: (file: File) => void;
}) {
  const [draft, setDraft] = useState({
    mode: choice.mode,
    kind: choice.kind,
    background: choice.background,
    instruction: choice.instruction,
    quality: choice.quality,
  });
  const dirty = Object.entries(draft).some(
    ([k, v]) => (choice as any)[k] !== v,
  );
  return (
    <div className="visual-editor">
      <strong>
        Место {choice.slotIndex + 1} · {names[choice.kind]}
      </strong>
      <p>{choice.description}</p>
      {choice.slot.detection?.method === "explicit_image_instruction" && (
        <p>
          Место изображения подтверждено надписью шаблона: «
          {choice.slot.detection.labels
            .map((text) => text.replace(/\s+/g, " "))
            .join("; ")}
          ». Служебная надпись будет убрана.
        </p>
      )}
      {choice.slot.protected ? (
        <p>
          Логотип или служебное изображение. Сохраняется исходное оформление.
        </p>
      ) : (
        <>
          <div className="visual-controls">
            <label>
              Источник
              <select
                value={draft.mode}
                disabled={busy}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    mode: e.target.value as typeof draft.mode,
                  })
                }
              >
                <option value="keep">Оставить оригинал</option>
                <option value="generate">GPT Image 2</option>
                <option value="upload" disabled={choice.mode !== "upload"}>
                  Мой файл
                </option>
              </select>
            </label>
            <label>
              Тип
              <select
                value={draft.kind}
                disabled={busy}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    kind: e.target.value as typeof draft.kind,
                  })
                }
              >
                {Object.entries(names).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Фон
              <select
                value={draft.background}
                disabled={busy || choice.slot.requiresOpaque}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    background: e.target.value as typeof draft.background,
                  })
                }
              >
                <option value="transparent">Прозрачный</option>
                <option value="opaque">С фоном</option>
              </select>
            </label>
            <label>
              Качество
              <select
                value={draft.quality}
                disabled={busy}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    quality: e.target.value as typeof draft.quality,
                  })
                }
              >
                <option value="low">Экономное</option>
                <option value="medium">Стандартное</option>
                <option value="high">Высокое</option>
              </select>
            </label>
          </div>
          <label>
            Что изобразить
            <textarea
              maxLength={1000}
              rows={3}
              disabled={busy}
              value={draft.instruction}
              placeholder="Необязательно: уточните предмет, процесс или интерфейс. Тему и стиль слайда учтём автоматически."
              onChange={(e) =>
                setDraft({ ...draft, instruction: e.target.value })
              }
            />
          </label>
          {choice.slot.requiresOpaque && (
            <small>
              Под этим изображением расположен текст. Для сохранения читаемости
              нужен непрозрачный фон.
            </small>
          )}
          <div className="visual-buttons">
            <button
              type="button"
              className="secondary"
              disabled={busy || !dirty}
              onClick={() => save(draft)}
            >
              Сохранить настройки
            </button>
            <label
              className={`secondary visual-upload ${busy ? "disabled" : ""}`}
            >
              Загрузить свой файл
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload(f);
                  e.target.value = "";
                }}
              />
            </label>
            {choice.mode === "generate" && choice.status && (
              <button
                type="button"
                className="text-button"
                disabled={busy || dirty}
                onClick={() => save({ ...draft, regenerate: true })}
              >
                Запросить новый вариант
              </button>
            )}
          </div>
          {dirty && (
            <small className="visual-warning">
              Сохраните настройки перед сборкой.
            </small>
          )}
          <small>
            PNG, JPEG или WebP до 24 МБ. Загрузка бесплатна. Новый AI-вариант
            будет оплачен при следующей сборке.
          </small>
        </>
      )}
      {choice.message && (
        <p className="visual-warning">
          {choice.message} В текущем результате сохранён оригинал.
        </p>
      )}
    </div>
  );
}
export function VisualPanel({
  id,
  revision,
  plan,
  dimensions,
  titles,
  busy,
  complete,
  request,
}: {
  id: string;
  revision: number;
  plan?: VisualPlan;
  dimensions?: { width: number; height: number };
  titles: string[];
  busy: boolean;
  complete: boolean;
  request: (path: string, body: unknown, method?: string) => void;
}) {
  const [slide, setSlide] = useState(0),
    [selected, setSelected] = useState(0);
  const choices =
    plan?.choices.filter((c) => c.slideIndex === slide && !c.preserveReason) ||
    [];
  const preserved = plan?.choices.filter((c) => c.preserveReason).length || 0;
  const choice = choices.find((c) => c.slotIndex === selected) || choices[0];
  const gen = plan?.choices.filter((c) => c.mode === "generate").length || 0;
  const base = `/projects/${id}`;
  const { width = 960, height = 540 } = dimensions || {};
  return (
    <section className="panel visual-panel" aria-label="Изображения слайдов">
      <div className="visual-heading">
        <div>
          <h3>Изображения слайдов</h3>
          <p>
            Система сама выбирает крупные изображения и их тип по теме, тексту и
            оформлению. Иконки и декор сохраняются.
          </p>
        </div>
        <span className="visual-model">GPT Image 2</span>
      </div>
      {!plan ? (
        <>
          <p>
            Определим места для тематических изображений. Логотипы и оформление
            сохраняются.
          </p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => request(`${base}/visuals/plan`, {})}
          >
            Определить места изображений
          </button>
        </>
      ) : (
        <>
          <p>
            Автоматически выбрано крупных изображений: <strong>{gen}</strong>.
            Сохранено иконок и элементов оформления:{" "}
            <strong>{preserved}</strong>. Выбирать тип и писать задания не
            требуется.
          </p>
          {busy && gen > 0 && (
            <p role="status">
              Готово изображений:{" "}
              {
                plan.choices.filter(
                  (c) => c.mode === "generate" && c.status === "ready",
                ).length
              }{" "}
              из {gen}. Генерация — до 3 изображений одновременно.
            </p>
          )}
          <details className="visual-options">
            <summary>
              Посмотреть места и изменить настройки (необязательно)
            </summary>
            <label>
              Слайд
              <select
                value={slide}
                disabled={busy}
                onChange={(e) => {
                  setSlide(Number(e.target.value));
                  setSelected(0);
                }}
              >
                {titles.map((title, i) => (
                  <option key={i} value={i}>
                    {i + 1}. {title}
                  </option>
                ))}
              </select>
            </label>
            <div className="visual-workspace">
              <div>
                <div
                  className="visual-map"
                  style={{ aspectRatio: `${width}/${height}` }}
                >
                  <img
                    src={`/api/projects/${id}/files/assembled/slide-${slide}.png`}
                    alt={`Места изображений на слайде ${slide + 1}`}
                  />
                  {choices.map((c) => (
                    <button
                      key={c.slotIndex}
                      type="button"
                      className={`visual-frame ${c.slotIndex === choice?.slotIndex ? "selected" : ""} ${c.slot.protected ? "protected" : ""}`}
                      style={{
                        left: `${(c.slot.box.x / width) * 100}%`,
                        top: `${(c.slot.box.y / height) * 100}%`,
                        width: `${(c.slot.box.w / width) * 100}%`,
                        height: `${(c.slot.box.h / height) * 100}%`,
                      }}
                      onClick={() => setSelected(c.slotIndex)}
                      aria-label={`Место изображения ${c.slotIndex + 1}`}
                    >
                      <span>{c.slotIndex + 1}</span>
                    </button>
                  ))}
                </div>
                <small>
                  Номера обозначают существующие места в макете. Геометрия и
                  слои сохраняются.
                </small>
                {!!choices.length && (
                  <div className="visual-place-list">
                    {choices.map((c) => (
                      <button
                        type="button"
                        key={c.slotIndex}
                        className={choice === c ? "selected" : ""}
                        onClick={() => setSelected(c.slotIndex)}
                      >
                        {c.slotIndex + 1} ·{" "}
                        {c.mode === "keep"
                          ? "Оригинал"
                          : c.mode === "upload"
                            ? "Мой файл"
                            : "AI"}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {choice ? (
                <div>
                  <ChoiceEditor
                    key={JSON.stringify(choice)}
                    choice={choice}
                    busy={busy}
                    save={(v) =>
                      request(
                        `${base}/visuals`,
                        {
                          revision,
                          slideIndex: slide,
                          slotIndex: choice.slotIndex,
                          ...(v as object),
                        },
                        "PUT",
                      )
                    }
                    upload={(file) => {
                      const form = new FormData();
                      form.append("file", file);
                      request(
                        `${base}/visuals/${revision}/${slide}/${choice.slotIndex}/upload`,
                        form,
                      );
                    }}
                  />
                  {choice.asset && (
                    <>
                      <img
                        className="visual-asset"
                        src={`/api/projects/${id}/files/visual/${choice.asset}`}
                        alt="Подготовленное изображение"
                      />
                      <small>
                        {choice.applied
                          ? "Вставлено в презентацию"
                          : "Готово к вставке при следующей сборке"}
                      </small>
                    </>
                  )}
                </div>
              ) : (
                <p>
                  На этом слайде нет крупных изображений для замены. Иконки,
                  текст, таблицы и диаграммы сохраняются как объекты PPTX.
                </p>
              )}
            </div>
          </details>
          <div className="visual-footer">
            <p>
              AI-мест: {gen}. Повторная сборка использует сохранённые картинки,
              если задание не изменилось. Схемы и интерфейсы генерируются как
              растровые иллюстрации.
            </p>
            <button
              className="primary"
              disabled={busy}
              onClick={() => request(`${base}/fill`, {})}
            >
              {complete
                ? "Обновить картинки и PPTX"
                : "Собрать с выбранными изображениями"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
