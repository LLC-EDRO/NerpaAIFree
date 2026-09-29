import { useEffect, useRef, useState } from "react";
import "./App.css";
import { ModelSettings } from "./settings/ModelSettings";
import { VisualPanel, type VisualPlan } from "./VisualPanel";
import { TokenUsagePanel, TokenTotals, type TokenUsage } from "./TokenUsage";
type Layout = {
  id: string;
  index: number;
  name: string;
  usable: boolean;
  warnings: string[];
  slots: { key: string; text: string; maxChars: number }[];
};
type Slide = {
  id?: string;
  sourceLayoutId: string;
  title: string;
  brief: string;
};
type Project = {
  id: string;
  name: string;
  status: string;
  busy: boolean;
  message: string;
  revision: number;
  progress?: { done: number; total: number };
  brief?: {
    topic: string;
    count: number;
    sourceText: string;
    webSearch?: boolean;
  };
  research?: {
    status: "ready" | "empty";
    searchedAt: string;
    sources: { id: string; url: string; title: string }[];
    evidence: { id: string; text: string; sourceIds: string[] }[];
  };
  analysis?: { layouts: Layout[]; warnings: string[] };
  plan?: { slides: Slide[] };
  contract?: {
    slides: unknown[];
    dimensions?: { width: number; height: number };
  };
  visuals?: VisualPlan;
  result?: {
    warnings?: Array<{slide?: number; key?: string; reason: string; message: string}>;
    pdfAvailable?: boolean;
    slides: number;
    expandedFields?: number;
    rebuiltSlides?: Array<{ slide: number; reason: string }>;
  };
  tokenUsage?: TokenUsage;
  error?: { message: string; details?: unknown };
  events: { at: string; message: string }[];
};
type Summary = { id: string; name: string; status: string; busy: boolean };
type Health = {
  sandbox: boolean;
  models: {
    provider: string;
    textModel: string;
    searchModel?: string;
    reasoning?: { normal: string; repair: string; search: string };
    visionModel: string;
    textConfigured: boolean;
    visionConfigured: boolean;
  };
};
async function api<T>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  const res = await fetch(
    "/api" + path,
    body === undefined
      ? undefined
      : {
          method,
          headers:
            body instanceof FormData
              ? {}
              : { "Content-Type": "application/json" },
          body: body instanceof FormData ? body : JSON.stringify(body),
        },
  );
  const data = await res.json().catch(() => {
    throw new Error(
      "Сервер временно недоступен. Проверьте, что backend запущен.",
    );
  });
  if (!res.ok)
    throw new Error(data.error?.message || "Не удалось выполнить запрос");
  return data;
}
const stages = ["Шаблон", "План и макеты", "Описание JSON", "Результат"];
function App() {
  const [settingsOpen,setSettingsOpen]=useState(location.hash === "#settings");
  useEffect(()=>{const update=()=>setSettingsOpen(location.hash === "#settings");window.addEventListener("hashchange",update);return()=>window.removeEventListener("hashchange",update);},[]);
  const [health, setHealth] = useState<Health>(),
    [projects, setProjects] = useState<Summary[]>([]),
    [project, setProject] = useState<Project>(),
    [id, setId] = useState(
      () => localStorage.getItem("nerpa-lab-project") || "",
    ),
    [tab, setTab] = useState(0),
    [topic, setTopic] = useState(""),
    [count, setCount] = useState(5),
    [sourceText, setSourceText] = useState(""),
    [webSearch, setWebSearch] = useState(true),
    [slides, setSlides] = useState<Slide[]>([]),
    [dirty, setDirty] = useState(false),
    [pending, setPending] = useState(false),
    [error, setError] = useState(""),
    [picker, setPicker] = useState<number | null>(null),
    [zoom, setZoom] = useState<{ url: string; title: string }>(),
    [showJson, setShowJson] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null),
    revision = useRef(""),
    currentId = useRef(id),
    dirtyRef = useRef(dirty);
  useEffect(() => {
    currentId.current = id;
    dirtyRef.current = dirty;
  }, [id, dirty]);
  const refreshList = () => api<Summary[]>("/projects").then(setProjects);
  useEffect(() => {
    void api<Health>("/health")
      .then(setHealth)
      .catch((e) => setError(String(e)));
    void refreshList();
  }, []);
  useEffect(() => {
    if (!id) return;
    localStorage.setItem("nerpa-lab-project", id);
    let cancelled = false;
    const update = async () => {
      try {
        const p = await api<Project>("/projects/" + id);
        if (cancelled || id !== currentId.current) return;
        setProject(p);
        const key = p.id + ":" + p.revision;
        if (revision.current !== key && !dirtyRef.current) {
          revision.current = key;
          setSlides(p.plan?.slides || []);
          setDirty(false);
          if (p.brief) {
            setTopic(p.brief.topic);
            setCount(p.brief.count);
            setSourceText(p.brief.sourceText);
            setWebSearch(p.brief.webSearch ?? true);
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    void update();
    const timer = setInterval(() => {
      void update();
    }, 1800);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    if (!project?.busy) void refreshList();
  }, [project?.busy]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setZoom(undefined);
        setPicker(null);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const blocked = pending || !!project?.busy,
    layouts = project?.analysis?.layouts || [],
    file = (kind: string, name: string) =>
      `/api/projects/${id}/files/${kind}/${name}`;
  const action = async (fn: () => Promise<void>) => {
    setError("");
    setPending(true);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };
  const upload = async (f: File) => {
    if (!f.name.toLowerCase().endsWith(".pptx"))
      throw new Error("Поддерживаются файлы .pptx");
    const form = new FormData();
    form.append("file", f);
    const p = await api<Project>("/projects", form);
    setProject(p);
    setId(p.id);
    setTab(0);
    revision.current = "";
    await refreshList();
  };
  const savePlan = async (assembleAfter = false) => {
    if (!project) throw new Error("Проект не выбран");
    if (!dirty) return project;
    const p = await api<Project>(
      `/projects/${id}/plan`,
      {
        revision: Number(revision.current.split(":")[1]),
        plan: { slides },
        assembleAfter,
      },
      "PUT",
    );
    revision.current = p.id + ":" + p.revision;
    setProject(p);
    setSlides(p.plan!.slides);
    setDirty(false);
    dirtyRef.current = false;
    return p;
  };
  const patch = (i: number, value: Partial<Slide>) => {
    setSlides((old) => old.map((s, j) => (j === i ? { ...s, ...value } : s)));
    setDirty(true);
  };
  const newProject = () => {
    if (dirty) {
      setError(
        "Сохраните изменения плана перед созданием нового эксперимента.",
      );
      return;
    }
    setId("");
    localStorage.removeItem("nerpa-lab-project");
    setProject(undefined);
    setSlides([]);
    setTopic("");
    setSourceText("");
    setTab(0);
    setError("");
    revision.current = "";
  };
  const ready =
    !!health?.sandbox &&
    health.models.textConfigured &&
    health.models.visionConfigured;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="/" className="brand">
          <span className="brand-mark">n</span>
          <span>
            nerpa<span className="brand-light">lab</span>
            <small>Лаборатория презентаций</small>
          </span>
        </a>
        <button className="new-button" onClick={()=>{location.hash="";newProject();}}>
          ＋ Новый эксперимент
        </button>
        <div className="nav-label">ВАШИ ЭКСПЕРИМЕНТЫ</div>
        <nav className="project-list" aria-label="Проекты">
          {projects.map((p) => (
            <button
              className={id === p.id ? "selected" : ""}
              key={p.id}
              onClick={() => {
                if (dirty) {
                  setError(
                    "Сохраните изменения плана перед переключением проекта.",
                  );
                  return;
                }
                location.hash="";
                setProject(undefined);
                setSlides([]);
                revision.current = "";
                setId(p.id);
                setTab(p.status === "complete" ? 3 : 0);
                setError("");
              }}
            >
              <span className={"project-dot " + (p.busy ? "pulsing" : "")} />
              <span>
                {p.name}
                <small>
                  {p.busy
                    ? "В работе"
                    : p.status === "complete"
                      ? "Готово"
                      : "Сохранён локально"}
                </small>
              </span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className="model-settings-link" onClick={()=>{location.hash="settings";}}>⚙ Настройки моделей</button>
          <span className="local-dot" /> Локальный прототип
          <p>Файлы хранятся на этом компьютере. База данных не используется.</p>
          <details>
            <summary>Модели и обработчик</summary>
            <p>
              Текст: {health?.models.textModel || "…"}
              <br />
              Изображения: {health?.models.visionModel || "…"}
              <br />
              Поиск: {health?.models.searchModel || "…"}
              <br />
              Reasoning: {health?.models.reasoning?.normal || "…"}, сложный
              ремонт: {health?.models.reasoning?.repair || "…"}
              <br />
              PPTX: {health?.sandbox ? "готов" : "недоступен"}
            </p>
          </details>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            Пользовательские шаблоны <span className="slash">/</span>{" "}
            {settingsOpen ? "Настройки моделей" : "Эксперимент"}
          </span>
          <span className="lab-tag">TEMPLATE LAB</span>
        </header>
        {settingsOpen && <ModelSettings onBack={()=>{location.hash="";}} onSaved={()=>{void api<Health>("/health").then(setHealth);}} />}
        <div className="workspace" style={settingsOpen ? {display:"none"} : undefined}>
          <div className="page-heading">
            <div className="eyebrow">ВАШ ДИЗАЙН. НОВОЕ СОДЕРЖАНИЕ.</div>
            <h1>От шаблона к презентации</h1>
            <p>
              Выберите макеты, утвердите историю — и проверьте, как AI заполнит
              ваш дизайн.
            </p>
          </div>
          <div className="steps" role="tablist" aria-label="Этапы">
            {stages.map((label, i) => (
              <button
                key={label}
                role="tab"
                aria-selected={tab === i}
                disabled={
                  (i === 1 && !project?.plan) ||
                  (i === 2 && !project?.contract) ||
                  (i === 3 && !project?.result)
                }
                onClick={() => setTab(i)}
                className={tab === i ? "active" : ""}
              >
                <span>0{i + 1}</span>
                {label}
                {(i === 0 && project?.analysis) ||
                (i === 1 && project?.contract) ||
                (i === 2 && project?.result) ? (
                  <b>✓</b>
                ) : null}
              </button>
            ))}
          </div>
          {(error || project?.error) && (
            <div className="notice error" role="alert">
              <strong>{error || project?.error?.message}</strong>
              {project?.error?.details != null && (
                <details>
                  <summary>Подробности проверки</summary>
                  <pre>{JSON.stringify(project.error.details, null, 2)}</pre>
                </details>
              )}
              {project?.error && !blocked && (
                <button
                  className="secondary"
                  onClick={() =>
                    void action(async () =>
                      setProject(
                        await api<Project>(`/projects/${id}/retry`, {}),
                      ),
                    )
                  }
                >
                  Повторить этап
                </button>
              )}
            </div>
          )}
          {project?.busy && (
            <div className="notice progress" role="status">
              <span className="spinner" />
              <div>
                <strong>{project.message}</strong>
                <small>
                  Можно переключать вкладки. Выполнение продолжится на сервере.
                </small>
                {project.progress && (
                  <progress
                    max={project.progress.total}
                    value={project.progress.done}
                  />
                )}
              </div>
              <button
                className="text-button"
                onClick={() =>
                  void action(async () => {
                    await api(`/projects/${id}/cancel`, {});
                  })
                }
              >
                Остановить
              </button>
            </div>
          )}
          {project && (
            <TokenUsagePanel usage={project.tokenUsage} busy={project.busy} />
          )}
          {project?.brief && (
            <section className="panel research-panel">
              <div className="research-heading">
                <div>
                  <strong>Источники и факты</strong>
                  <p>
                    {project.research
                      ? `Фактов: ${project.research.evidence.length} · поиск от ${new Date(project.research.searchedAt).toLocaleDateString("ru-RU")}`
                      : "Можно дополнить тему сведениями из интернета, сохранив утверждённые макеты."}
                  </p>
                </div>
                {!project.research && (
                  <button
                    className="secondary"
                    disabled={blocked || dirty}
                    onClick={() =>
                      void action(async () => {
                        setProject(
                          await api<Project>(`/projects/${id}/research`, {}),
                        );
                        setWebSearch(true);
                      })
                    }
                  >
                    Найти источники
                  </button>
                )}
                {project.research && (
                  <a href={file("json", "research")} download="research.json">
                    Скачать источники ↓
                  </a>
                )}
              </div>
              {project.research?.status === "empty" && (
                <p>
                  Поиск не вернул фактов с подтверждёнными ссылками.
                  Используются только ваши материалы.
                </p>
              )}
              {!!project.research?.evidence.length && (
                <details>
                  <summary>Посмотреть найденные факты и ссылки</summary>
                  <ol className="research-facts">
                    {project.research.evidence.map((e) => (
                      <li key={e.id}>
                        <p>{e.text}</p>
                        <div>
                          {e.sourceIds.map((sourceId) => {
                            const source = project.research!.sources.find(
                              (s) => s.id === sourceId,
                            );
                            return source ? (
                              <a
                                key={source.id}
                                href={source.url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                {source.title} ↗
                              </a>
                            ) : null;
                          })}
                        </div>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </section>
          )}
          {tab === 0 && (
            <>
              <div className="setup-grid">
                <section className="panel source-panel">
                  <div className="section-heading">
                    <span className="section-number">01</span>
                    <h2>Исходный шаблон</h2>
                  </div>
                  {!project ? (
                    <div
                      className="dropzone"
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        e.preventDefault();
                        const f = e.dataTransfer.files[0];
                        if (f && !pending) void action(() => upload(f));
                      }}
                    >
                      <div className="file-symbol">P</div>
                      <h3>Добавьте презентацию</h3>
                      <p>
                        Перетащите сюда ваш PPTX
                        <br />
                        или выберите файл на компьютере
                      </p>
                      <button
                        disabled={pending}
                        className="primary"
                        onClick={() => fileInput.current?.click()}
                      >
                        {pending ? "Загружаем…" : "Выбрать шаблон"}
                      </button>
                      <small>PPTX · до 40 МБ · до 100 исходных слайдов</small>
                    </div>
                  ) : (
                    <div className="uploaded">
                      <div className="file-symbol small">P</div>
                      <div>
                        <h3>{project.name}</h3>
                        <p>
                          {project.analysis
                            ? `${layouts.length} макетов · ${layouts.filter((l) => l.usable).length} доступны для заполнения`
                            : "Подготавливаем превью и структуру"}
                        </p>
                      </div>
                      <span className="check">✓</span>
                    </div>
                  )}
                  <input
                    aria-label="Файл шаблона PPTX"
                    ref={fileInput}
                    type="file"
                    accept=".pptx"
                    hidden
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void action(() => upload(f));
                      e.target.value = "";
                    }}
                  />
                  <div className="preservation">
                    <strong>Оформление остаётся вашим</strong>
                    <p>
                      Родные текстовые поля, фон, фотографии, диаграммы и
                      декоративные элементы сохраняются. Новый текст проверяется
                      на вместимость.
                    </p>
                  </div>
                  {project?.analysis?.warnings?.length ? (
                    <details className="warnings">
                      <summary>
                        Особенности шаблона ({project.analysis.warnings.length})
                      </summary>
                      {project.analysis.warnings.map((w) => (
                        <p key={w}>{w}</p>
                      ))}
                    </details>
                  ) : null}
                </section>
                <section className="panel brief-panel">
                  <div className="section-heading">
                    <span className="section-number">02</span>
                    <h2>Новая история</h2>
                  </div>
                  <label>
                    Тема презентации
                    <textarea
                      disabled={blocked}
                      rows={2}
                      placeholder="Например: результаты команды за первое полугодие"
                      value={topic}
                      onChange={(e) => setTopic(e.target.value)}
                    />
                  </label>
                  <label className="count-label">
                    Количество слайдов{" "}
                    <input
                      disabled={blocked}
                      type="number"
                      min={1}
                      max={20}
                      value={count}
                      onChange={(e) => setCount(Number(e.target.value))}
                    />
                  </label>
                  <small className="hint">
                    От 1 до 20, включая титульный. Макеты могут повторяться.
                  </small>
                  <label>
                    Материалы и факты{" "}
                    <span className="optional">необязательно</span>
                    <textarea
                      disabled={blocked}
                      rows={6}
                      placeholder="Добавьте тезисы, цифры с единицами, названия и важные детали. AI не будет брать статистику из исходного шаблона."
                      value={sourceText}
                      onChange={(e) => setSourceText(e.target.value)}
                    />
                  </label>
                  <label className="search-toggle">
                    <input
                      type="checkbox"
                      checked={webSearch}
                      disabled={blocked}
                      onChange={(e) => setWebSearch(e.target.checked)}
                    />
                    <span>
                      Искать факты и статистику в интернете
                      <small>
                        Сохраним найденные источники. Поиск может занять
                        несколько минут.
                      </small>
                    </span>
                  </label>
                  <div className="form-footer">
                    <span>План можно будет отредактировать</span>
                    <button
                      className="primary"
                      disabled={
                        blocked ||
                        !project?.analysis ||
                        topic.trim().length < 3 ||
                        !ready ||
                        count < 1 ||
                        count > 20
                      }
                      onClick={() =>
                        void action(async () => {
                          setProject(
                            await api<Project>(`/projects/${id}/plan`, {
                              topic,
                              count,
                              sourceText,
                              webSearch,
                            }),
                          );
                          setDirty(false);
                        })
                      }
                    >
                      Составить план ↗
                    </button>
                  </div>
                  {!ready && (
                    <p className="warning-text">
                      Для запуска нужны Docker-обработчик и настроенные ключи
                      моделей.
                    </p>
                  )}
                  {project?.plan && (
                    <button
                      className="text-button next"
                      onClick={() => setTab(1)}
                    >
                      Открыть план →
                    </button>
                  )}
                </section>
              </div>
              {layouts.length > 0 && (
                <section className="catalog">
                  <div className="catalog-heading">
                    <h2>
                      Макеты вашего шаблона <span>{layouts.length}</span>
                    </h2>
                    <p>Нажмите на слайд, чтобы рассмотреть оформление</p>
                  </div>
                  <div className="layout-grid">
                    {layouts.map((l) => (
                      <button
                        className={
                          "layout-card " + (!l.usable ? "unavailable" : "")
                        }
                        key={l.id}
                        onClick={() =>
                          setZoom({
                            url: file("source", `slide-${l.index}.png`),
                            title: `Макет ${l.index + 1}: ${l.name}`,
                          })
                        }
                      >
                        <div className="thumbnail">
                          <img
                            src={file("source", `slide-${l.index}.png`)}
                            alt={l.name}
                            loading="lazy"
                          />
                          <span className="slide-index">
                            {String(l.index + 1).padStart(2, "0")}
                          </span>
                        </div>
                        <div className="layout-caption">
                          <strong>{l.name}</strong>
                          <span>
                            {l.usable
                              ? `${l.slots.length} текстовых полей`
                              : "Только просмотр"}
                          </span>
                        </div>
                        {!l.usable && (
                          <p className="layout-warning">{l.warnings[0]}</p>
                        )}
                      </button>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
          {tab === 1 && project?.plan && (
            <section>
              <div className="stage-header">
                <div>
                  <h2>История и визуальный ритм</h2>
                  <p>
                    Отредактируйте смысл слайдов и выберите подходящие макеты.
                    Порядок можно менять стрелками. После сохранения AI
                    автоматически адаптирует план под изменения.
                  </p>
                </div>
                <button
                  className="secondary"
                  disabled={blocked || !dirty}
                  onClick={() =>
                    void action(async () => {
                      await savePlan();
                    })
                  }
                >
                  {dirty ? "Сохранить изменения" : "Сохранено ✓"}
                </button>
              </div>
              <div className="plan-list">
                {slides.map((s, i) => {
                  const l = layouts.find((l) => l.id === s.sourceLayoutId);
                  return (
                    <article className="plan-slide" key={s.id || i}>
                      <div className="plan-order">
                        <b>{String(i + 1).padStart(2, "0")}</b>
                        <button
                          aria-label={`Слайд ${i + 1} вверх`}
                          disabled={blocked || i === 0}
                          onClick={() => {
                            setSlides((prev) => {
                              const next = [...prev];
                              [next[i - 1], next[i]] = [next[i], next[i - 1]];
                              return next;
                            });
                            setDirty(true);
                          }}
                        >
                          ↑
                        </button>
                        <button
                          aria-label={`Слайд ${i + 1} вниз`}
                          disabled={blocked || i === slides.length - 1}
                          onClick={() => {
                            setSlides((prev) => {
                              const next = [...prev];
                              [next[i + 1], next[i]] = [next[i], next[i + 1]];
                              return next;
                            });
                            setDirty(true);
                          }}
                        >
                          ↓
                        </button>
                      </div>
                      <button
                        className="plan-preview"
                        disabled={blocked}
                        onClick={() => setPicker(i)}
                      >
                        <img
                          src={file("source", `slide-${l?.index || 0}.png`)}
                          alt={`Макет слайда ${i + 1}`}
                        />
                        <span>
                          Макет {(l?.index || 0) + 1} <b>Сменить ↗</b>
                        </span>
                      </button>
                      <div className="plan-fields">
                        <label>
                          Заголовок слайда {i + 1}
                          <input
                            disabled={blocked}
                            value={s.title}
                            onChange={(e) =>
                              patch(i, { title: e.target.value })
                            }
                          />
                        </label>
                        <label>
                          Что раскрываем
                          <textarea
                            disabled={blocked}
                            rows={3}
                            value={s.brief}
                            onChange={(e) =>
                              patch(i, { brief: e.target.value })
                            }
                          />
                        </label>
                      </div>
                    </article>
                  );
                })}
              </div>
              <div className="action-bar">
                <div>
                  <strong>{slides.length} слайдов в новой презентации</strong>
                  <p>
                    Следующий шаг: сборка PPTX и визуальное описание каждого
                    выбранного слайда.
                  </p>
                </div>
                <button
                  className="primary"
                  disabled={blocked}
                  onClick={() =>
                    void action(async () => {
                      if (dirty) {
                        // One server job continues through AI adaptation and
                        // assembly even after the browser is reloaded.
                        await savePlan(true);
                      } else {
                        setProject(
                          await api<Project>(`/projects/${id}/assemble`, {
                            revision: project.revision,
                          }),
                        );
                      }
                    })
                  }
                >
                  Утвердить и проанализировать ↗
                </button>
              </div>
              {project.contract && (
                <button className="text-button next" onClick={() => setTab(2)}>
                  Открыть описание шаблона →
                </button>
              )}
            </section>
          )}
          {tab === 2 && project?.contract && (
            <section>
              <div className="stage-header">
                <div>
                  <h2>Шаблон, понятный модели</h2>
                  <p>
                    Макеты уже собраны в один PPTX. Модель описала смысл полей
                    по изображениям и точной структуре.
                  </p>
                </div>
                <a
                  className="secondary"
                  href={file("json", "template")}
                  download="template.json"
                >
                  ↓ JSON шаблона
                </a>
              </div>
              <div className="contract-banner">
                <span className="check large">✓</span>
                <div>
                  <h3>Описание готово</h3>
                  <p>
                    {project.contract.slides.length} слайдов · координаты и
                    стили взяты из PPTX · роли полей определены AI
                  </p>
                </div>
                <a
                  href={file("assembled", "presentation.pptx")}
                  className="text-button"
                  download
                >
                  Скачать собранный макет ↓
                </a>
              </div>
              <div className="preview-strip">
                {project.contract.slides.map((_, i) => (
                  <button
                    key={i}
                    onClick={() =>
                      setZoom({
                        url: file("assembled", `slide-${i}.png`),
                        title: `Собранный макет · слайд ${i + 1}`,
                      })
                    }
                  >
                    <img
                      src={file("assembled", `slide-${i}.png`)}
                      alt={`Собранный макет ${i + 1}`}
                    />
                    <small>{i + 1}</small>
                  </button>
                ))}
              </div>
              <button
                className="text-button"
                onClick={() => setShowJson(!showJson)}
              >
                {showJson ? "Скрыть" : "Посмотреть"} описание JSON{" "}
                {showJson ? "−" : "+"}
              </button>
              {showJson && (
                <pre className="json-view">
                  {JSON.stringify(project.contract, null, 2)}
                </pre>
              )}
              <div className="action-bar">
                <div>
                  <strong>Теперь заполним этот макет</strong>
                  <p>
                    Тексты и данные будут проверены до экспорта. Исходные
                    изображения заменятся согласно настройкам ниже.
                  </p>
                </div>
                <button
                  className="primary"
                  disabled={blocked}
                  onClick={() =>
                    void action(async () =>
                      setProject(
                        await api<Project>(`/projects/${id}/fill`, {}),
                      ),
                    )
                  }
                >
                  Создать презентацию ↗
                </button>
              </div>
              {project.result && (
                <button className="text-button next" onClick={() => setTab(3)}>
                  Открыть результат →
                </button>
              )}
            </section>
          )}
          {(tab === 2 || tab === 3) && project?.contract && (
            <VisualPanel
              key={`${id}:${project.revision}`}
              id={id}
              revision={project.revision}
              plan={project.visuals}
              dimensions={project.contract.dimensions}
              titles={project.plan?.slides.map((s) => s.title) || []}
              busy={blocked}
              complete={!!project.result}
              request={(path, body, method) =>
                void action(async () =>
                  setProject(await api<Project>(path, body, method)),
                )
              }
            />
          )}
          {tab === 3 && project?.result && (
            <section>
              <div className="stage-header">
                <div>
                  <div className="eyebrow">ГОТОВО К ПРОСМОТРУ</div>
                  <h2>Ваша новая презентация</h2>
                  <p>
                    Проверьте формулировки и факты. Текст, таблицы и диаграммы
                    остаются редактируемыми.
                  </p>
                </div>
                <div className="download-actions">
                  {project.result.pdfAvailable !== false && <a
                    className="secondary"
                    href={file("output", "presentation.pdf")}
                    download
                  >
                    ↓ PDF
                  </a>}
                  <a
                    className="primary"
                    href={file("output", "presentation.pptx")}
                    download
                  >
                    ↓ Скачать PPTX
                  </a>
                </div>
              </div>
              <div className="token-result">
                {!!project.result.warnings?.length && (
                  <div className="quality-warnings" role="status">
                    <strong>Презентация создана. Проверьте замечания</strong>
                    <p>Файл можно скачать и отредактировать в PowerPoint.</p>
                    <ul>
                      {project.result.warnings.map((item, index) => (
                        <li key={index}>
                          {item.slide && project.result?.pdfAvailable !== false ? <button className="warning-slide" onClick={() => setZoom({url:file("output",`slide-${item.slide! - 1}.png`),title:`Слайд ${item.slide}`})}>Слайд {item.slide}</button> : item.slide ? `Слайд ${item.slide}` : 'Презентация'}: {item.message}
                        </li>
                      ))}
                    </ul>
                    <a href={file("output", "quality-warnings.json")} download>Скачать замечания</a>
                  </div>
                )}
                {!!project.result.rebuiltSlides?.length && (
                  <div role="status">
                    <p>
                      Автоматические изменения и сохранённые варианты слайдов:
                    </p>
                    <ul>
                      {project.result.rebuiltSlides.map((item) => (
                        <li key={item.slide}>
                          Слайд {item.slide}: {item.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!project.result.expandedFields && (
                  <p>
                    Автоматически расширено полей:{" "}
                    {project.result.expandedFields}.{" "}
                    <a href={file("output", "field-changes.json")} download>
                      Скачать изменения размеров
                    </a>
                  </p>
                )}
                <TokenTotals usage={project.tokenUsage} final />
              </div>
              {project.result.pdfAvailable !== false && <div className="result-grid">
                {Array.from({ length: project.result.slides }, (_, i) => (
                  <button
                    key={i}
                    onClick={() =>
                      setZoom({
                        url: file("output", `slide-${i}.png`),
                        title: slides[i]?.title || `Слайд ${i + 1}`,
                      })
                    }
                  >
                    <img
                      src={file("output", `slide-${i}.png`)}
                      alt={`Готовый слайд ${i + 1}`}
                    />
                    <span>
                      <b>{String(i + 1).padStart(2, "0")}</b> {slides[i]?.title}
                    </span>
                  </button>
                ))}
              </div>
              }
              <div className="artifact-links">
                <a href={file("json", "filled")} download="filled.json">
                  JSON заполнения ↗
                </a>
                <a href={file("json", "template")} download="template.json">
                  JSON шаблона ↗
                </a>
                <a href={file("output", "render-quality.json")} download>
                  Проверка рендеринга ↗
                </a>
              </div>
            </section>
          )}
          {project && (
            <details className="timeline">
              <summary>
                История этапов <span>{project.events.length}</span>
              </summary>
              {project.events
                .slice()
                .reverse()
                .map((event, i) => (
                  <div key={i}>
                    <time>
                      {new Date(event.at).toLocaleTimeString("ru-RU")}
                    </time>
                    <span>{event.message}</span>
                  </div>
                ))}
            </details>
          )}
          <footer>
            NERPA LAB{" "}
            <span>
              Исходный шаблон → утверждённый макет → описание → заполнение
            </span>
          </footer>
        </div>
      </main>
      {picker !== null && (
        <div className="modal-backdrop" onClick={() => setPicker(null)}>
          <section
            className="modal picker-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Выбор макета"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <div>
                <h2>Макет для слайда {picker + 1}</h2>
                <p>{slides[picker]?.title}</p>
              </div>
              <button
                aria-label="Закрыть выбор макета"
                onClick={() => setPicker(null)}
              >
                ×
              </button>
            </div>
            <div className="layout-grid">
              {layouts
                .filter((l) => l.usable)
                .map((l) => (
                  <button
                    className={
                      "layout-card " +
                      (slides[picker].sourceLayoutId === l.id ? "chosen" : "")
                    }
                    key={l.id}
                    onClick={() => {
                      patch(picker, { sourceLayoutId: l.id });
                      setPicker(null);
                    }}
                  >
                    <img
                      src={file("source", `slide-${l.index}.png`)}
                      alt={l.name}
                    />
                    <div className="layout-caption">
                      <strong>
                        {l.index + 1}. {l.name}
                      </strong>
                      <span>{l.slots.length} полей</span>
                    </div>
                  </button>
                ))}
            </div>
          </section>
        </div>
      )}
      {zoom && (
        <div className="modal-backdrop" onClick={() => setZoom(undefined)}>
          <section
            className="modal zoom-modal"
            role="dialog"
            aria-modal="true"
            aria-label={zoom.title}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-heading">
              <h2>{zoom.title}</h2>
              <button
                aria-label="Закрыть просмотр"
                onClick={() => setZoom(undefined)}
              >
                ×
              </button>
            </div>
            <img src={zoom.url} alt={zoom.title} />
          </section>
        </div>
      )}
    </div>
  );
}
export default App;
