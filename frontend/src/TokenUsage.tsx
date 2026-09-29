export type TokenCounts = {
  inputTokens: number;
  outputTokens: number;
  requests: number;
  missingInput: number;
  missingOutput: number;
};
type CostCounts = {
  inputUsd: number;
  outputUsd: number;
  searchUsd: number;
  totalUsd: number;
  searchCalls: number;
  cachedInputTokens: number;
  cacheWriteTokens: number;
  missingInput: number;
  missingOutput: number;
  missingSearch: number;
};
export type TokenUsage = {
  cost?: CostCounts;
  pricing?: {
    model?: string;
    models?: Record<
      string,
      {
        inputPerMillion: number;
        cachedInputPerMillion: number;
        cacheWritePerMillion: number;
        outputPerMillion: number;
      }
    >;
    image?: {
      textInputPerMillion: number;
      imageInputPerMillion: number;
      outputPerMillion: number;
    };
    checkedAt: string;
    source: string;
    modelSource: string;
    inputPerMillion: number;
    cachedInputPerMillion: number;
    cacheWritePerMillion: number;
    outputPerMillion: number;
    webSearchPerCall: number;
  };
  total: TokenCounts;
  stages: (TokenCounts & { id: string; label: string; cost?: CostCounts })[];
};
function count(value: number, missing: number, requests: number) {
  if (missing && missing === requests) return "Нет данных";
  return `${missing ? "≥ " : ""}${value.toLocaleString("ru-RU")}`;
}
function TokensOnly({
  usage,
  final = false,
}: {
  usage?: TokenUsage;
  final?: boolean;
}) {
  return (
    <div
      className="token-totals"
      aria-label={final ? "Всего токенов за проект" : "Расход токенов проекта"}
    >
      <strong>{final ? "Всего за проект" : "Расход токенов"}</strong>
      <span>
        Input{" "}
        <b>
          {usage
            ? count(
                usage.total.inputTokens,
                usage.total.missingInput,
                usage.total.requests,
              )
            : "…"}
        </b>
      </span>
      <span>
        Output{" "}
        <b>
          {usage
            ? count(
                usage.total.outputTokens,
                usage.total.missingOutput,
                usage.total.requests,
              )
            : "…"}
        </b>
      </span>
    </div>
  );
}
function dollars(value: number | undefined, missing = 0) {
  if (value === undefined) return "…";
  if (missing && value === 0) return "Нет данных";
  return `${missing ? "≥ " : ""}${value > 0 && value < 0.0001 ? "< $0.0001" : `$${value.toFixed(4)}`}`;
}
function missingCost(cost?: CostCounts) {
  return cost ? cost.missingInput + cost.missingOutput + cost.missingSearch : 0;
}
export function TokenTotals({
  usage,
  final = false,
}: {
  usage?: TokenUsage;
  final?: boolean;
}) {
  const cost = usage?.cost;
  return (
    <div className="usage-summary">
      <div
        className="cost-totals"
        aria-label="Расчётная стоимость проекта в долларах"
      >
        <strong>{final ? "Стоимость проекта" : "Стоимость, USD"}</strong>
        <b className="cost-total">
          {dollars(cost?.totalUsd, missingCost(cost))}
        </b>
        <dl>
          <div>
            <dt>Input · с учётом кэша</dt>
            <dd>{dollars(cost?.inputUsd, cost?.missingInput)}</dd>
          </div>
          <div>
            <dt>Output</dt>
            <dd>{dollars(cost?.outputUsd, cost?.missingOutput)}</dd>
          </div>
          <div>
            <dt>Web Search{cost ? ` · ${cost.searchCalls} выз.` : ""}</dt>
            <dd>{dollars(cost?.searchUsd, cost?.missingSearch)}</dd>
          </div>
        </dl>
        <small>Расчёт по тарифам API, включая повторы.</small>
        {missingCost(cost) > 0 && (
          <small className="cost-incomplete">
            Часть расходов неизвестна. «≥» — известная сумма.
          </small>
        )}
      </div>
      <TokensOnly usage={usage} final={final} />
    </div>
  );
}
export function TokenUsagePanel({
  usage,
  busy,
}: {
  usage?: TokenUsage;
  busy: boolean;
}) {
  return (
    <section className="panel token-panel" aria-label="Токены по этапам">
      <TokenTotals usage={usage} />
      <p>
        Input — входящие, Output — исходящие. За всю историю проекта, включая
        поиск, повторы и автоисправления.
        {busy ? " Обновляются после ответа модели." : ""}
      </p>
      {usage && (
        <>
          <div className="token-table-wrap">
            <table className="token-table">
              <thead>
                <tr>
                  <th scope="col">Этап</th>
                  <th scope="col">Input</th>
                  <th scope="col">Output</th>
                  <th scope="col">USD</th>
                </tr>
              </thead>
              <tbody>
                {usage.stages.map((row) => (
                  <tr key={row.id}>
                    <th scope="row">{row.label}</th>
                    <td>
                      {count(row.inputTokens, row.missingInput, row.requests)}
                    </td>
                    <td>
                      {count(row.outputTokens, row.missingOutput, row.requests)}
                    </td>
                    <td>
                      {dollars(row.cost?.totalUsd, missingCost(row.cost))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <small>
            По ответам провайдера. Кэшированные входящие токены и токены
            рассуждения уже входят в соответствующие суммы. Разбор и экспорт без
            нейросети — 0 токенов.
          </small>
          {usage.pricing && (
            <details className="cost-pricing">
              <summary>Как рассчитана цена</summary>
              {Object.entries(
                usage.pricing.models || {
                  [usage.pricing.model || "Luna"]: usage.pricing,
                },
              ).map(([model, rates]) => (
                <p key={model}>
                  {model} за 1 млн токенов: input ${rates.inputPerMillion},
                  кэшированный input ${rates.cachedInputPerMillion}, запись в
                  кэш ${rates.cacheWritePerMillion}, output $
                  {rates.outputPerMillion}.
                </p>
              ))}
              <p>
                Каждый запрос рассчитан по фактически использованной модели. Web
                Search: ${usage.pricing.webSearchPerCall.toFixed(2)} за вызов
                инструмента; его токены уже включены в Input и Output.
              </p>
              {usage.pricing.image && (
                <p>
                  GPT Image 2 за 1 млн токенов: текстовый input $
                  {usage.pricing.image.textInputPerMillion}, input изображений $
                  {usage.pricing.image.imageInputPerMillion}, output $
                  {usage.pricing.image.outputPerMillion}. Генерация картинок
                  включена в общую стоимость по usage API.
                </p>
              )}
              <p>
                Учтены все сохранённые ответы API, включая повторные попытки.
                При input свыше 272 000 токенов в одном запросе тариф input ×2,
                output ×1,5. Fast: ×2, Flex: ×0,5. Для старых ответов без
                указания режима принят Standard. Это оценка по публичным тарифам
                от {usage.pricing.checkedAt}, без налогов и индивидуальных
                скидок. Запросы без полученного ответа с usage могут не попасть
                в расчёт.
              </p>
              <a href={usage.pricing.source} target="_blank" rel="noreferrer">
                Тарифы OpenAI ↗
              </a>
            </details>
          )}
          {(usage.total.missingInput > 0 || usage.total.missingOutput > 0) && (
            <p className="token-incomplete">
              Для части запросов расход недоступен. «≥» означает сумму известных
              токенов; неизвестный расход не считается нулевым.
            </p>
          )}
        </>
      )}
    </section>
  );
}
