export type TokenCounts = {
  inputTokens: number;
  outputTokens: number;
  requests: number;
  missingInput: number;
  missingOutput: number;
};
export type TokenUsage = {
  total: TokenCounts;
  stages: (TokenCounts & { id: string; label: string })[];
};
function count(value: number, missing: number, requests: number) {
  if (missing && missing === requests) return 'Нет данных';
  return `${missing ? '≥ ' : ''}${value.toLocaleString('ru-RU')}`;
}
export function TokenTotals({usage, final = false}: {usage?: TokenUsage; final?: boolean}) {
  return <div className="token-totals" aria-label={final ? 'Всего токенов за проект' : 'Расход токенов проекта'}>
    <strong>{final ? 'Всего за проект' : 'Расход токенов'}</strong>
    <span>Input <b>{usage ? count(usage.total.inputTokens, usage.total.missingInput, usage.total.requests) : '…'}</b></span>
    <span>Output <b>{usage ? count(usage.total.outputTokens, usage.total.missingOutput, usage.total.requests) : '…'}</b></span>
  </div>;
}
export function TokenUsagePanel({usage, busy}: {usage?: TokenUsage; busy: boolean}) {
  return <section className="panel token-panel" aria-label="Токены по этапам">
    <TokenTotals usage={usage} />
    <p>Input — входящие, Output — исходящие. За всю историю проекта, включая повторы и автоисправления.{busy ? ' Обновляются после ответа модели.' : ''}</p>
    {usage && <>
      <div className="token-table-wrap"><table className="token-table">
        <thead><tr><th scope="col">Этап</th><th scope="col">Input</th><th scope="col">Output</th></tr></thead>
        <tbody>{usage.stages.map(row => <tr key={row.id}>
          <th scope="row">{row.label}</th>
          <td>{count(row.inputTokens, row.missingInput, row.requests)}</td>
          <td>{count(row.outputTokens, row.missingOutput, row.requests)}</td>
        </tr>)}</tbody>
      </table></div>
      <small>По ответам локальной модели. Разбор и экспорт без модели — 0 токенов.</small>
      {(usage.total.missingInput > 0 || usage.total.missingOutput > 0) && <p className="token-incomplete">Для части запросов расход недоступен. «≥» означает сумму известных токенов.</p>}
    </>}
  </section>;
}
