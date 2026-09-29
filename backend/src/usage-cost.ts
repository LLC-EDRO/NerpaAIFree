// Public list prices verified 2026-09-24. USD per million tokens.
// Historical receipts are estimated at this snapshot, not an invoice lookup.
// Keep historical model rates: mixed-model projects are priced receipt by receipt.
export const textModelRates: Record<
  string,
  {
    inputPerMillion: number;
    cachedInputPerMillion: number;
    cacheWritePerMillion: number;
    outputPerMillion: number;
  }
> = {
  "gpt-5.6-luna": {
    inputPerMillion: 0.2,
    cachedInputPerMillion: 0.02,
    cacheWritePerMillion: 0.25,
    outputPerMillion: 1.2,
  },
  "gpt-6-luna": {
    inputPerMillion: 0.1,
    cachedInputPerMillion: 0.01,
    cacheWritePerMillion: 0.125,
    outputPerMillion: 0.5,
  },
};
export const pricing = {
  checkedAt: "2026-09-24",
  source: "https://developers.openai.com/api/docs/pricing",
  modelSource: "https://developers.openai.com/api/docs/models/gpt-6-luna",
  model: "gpt-6-luna",
  ...textModelRates["gpt-6-luna"],
  models: textModelRates,
  webSearchPerCall: 0.01,
  longContextThreshold: 272000,
  image: {
    model: "gpt-image-2",
    textInputPerMillion: 5,
    imageInputPerMillion: 8,
    cachedTextPerMillion: 1.25,
    cachedImagePerMillion: 2,
    outputPerMillion: 30,
    source:
      "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst",
  },
} as const;
export type CostCounts = {
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
export const emptyCost = (): CostCounts => ({
  inputUsd: 0,
  outputUsd: 0,
  searchUsd: 0,
  totalUsd: 0,
  searchCalls: 0,
  cachedInputTokens: 0,
  cacheWriteTokens: 0,
  missingInput: 0,
  missingOutput: 0,
  missingSearch: 0,
});
const tokens = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : undefined;
const round = (v: number) => Math.round(v * 1e12) / 1e12;
export function addCost(target: CostCounts, value: CostCounts) {
  for (const key of Object.keys(target) as (keyof CostCounts)[])
    target[key] = round(target[key] + value[key]);
}
export function receiptCost(record: any): CostCounts {
  const cost = emptyCost();
  if (record?.model === "gpt-image-2") return imageReceiptCost(record);
  const response = record?.response;
  const usage = record?.usage ?? response?.usage;
  const model = record?.responseModel ?? response?.model ?? record?.model;
  const tier = record?.serviceTier ?? response?.service_tier ?? "default";
  // Old chat receipts used the default request tier and stored the requested model.
  const multiplier =
    tier === "default"
      ? 1
      : tier === "flex"
        ? 0.5
        : tier === "priority" || tier === "fast"
          ? 2
          : undefined;
  const rates =
    typeof model === "string" && Object.hasOwn(textModelRates, model)
      ? textModelRates[model]
      : undefined;
  const known =
    rates !== undefined &&
    multiplier !== undefined &&
    (!record?.provider || record.provider === "openai");
  const input = tokens(usage?.input_tokens) ?? tokens(usage?.prompt_tokens);
  const output =
    tokens(usage?.output_tokens) ?? tokens(usage?.completion_tokens);
  const detail = usage?.input_tokens_details ?? usage?.prompt_tokens_details;
  const cached = tokens(detail?.cached_tokens);
  // Cache-write detail is optional; older API responses predate this counter.
  const written =
    detail?.cache_write_tokens === undefined
      ? 0
      : tokens(detail.cache_write_tokens);
  const validCache =
    input !== undefined &&
    cached !== undefined &&
    written !== undefined &&
    cached + written <= input;
  if (known && validCache) {
    const long = input! > pricing.longContextThreshold ? 2 : 1;
    cost.cachedInputTokens = cached!;
    cost.cacheWriteTokens = written!;
    cost.inputUsd = round(
      (((input! - cached! - written!) * rates!.inputPerMillion +
        cached! * rates!.cachedInputPerMillion +
        written! * rates!.cacheWritePerMillion) *
        long *
        multiplier!) /
        1e6,
    );
  } else if (known && input === 0) {
    cost.inputUsd = 0;
  } else cost.missingInput = 1;
  // The request input count determines long-context output pricing as well.
  if (known && output !== undefined && input !== undefined)
    cost.outputUsd = round(
      (output *
        rates!.outputPerMillion *
        (input > pricing.longContextThreshold ? 1.5 : 1) *
        multiplier!) /
        1e6,
    );
  else cost.missingOutput = 1;
  if (record?.stage === "web-search") {
    if (
      Array.isArray(response?.output) &&
      (!record?.provider || record.provider === "openai")
    ) {
      // Count tool invocations, never citations, source URLs or search query strings.
      const calls = response.output.filter(
        (item: any) => item?.type === "web_search_call",
      );
      const ids = new Set<string>();
      for (const call of calls) {
        if (typeof call.id === "string") {
          if (ids.has(call.id)) continue;
          ids.add(call.id);
        }
        cost.searchCalls++;
      }
      cost.searchUsd = round(cost.searchCalls * pricing.webSearchPerCall);
    } else cost.missingSearch = 1;
  } else if (!record || !record.stage) cost.missingSearch = 1;
  // Search content and reasoning are already included in the provider token totals.
  cost.totalUsd = round(cost.inputUsd + cost.outputUsd + cost.searchUsd);
  return cost;
}

// Standard GPT Image 2 rates. Unlike Luna, image input/output have their own rates.
function imageReceiptCost(record: any): CostCounts {
  const cost = emptyCost(),
    u = record.usage,
    d = u?.input_tokens_details;
  const input = tokens(u?.input_tokens),
    text = tokens(d?.text_tokens),
    image = tokens(d?.image_tokens),
    output = tokens(u?.output_tokens);
  const cached = tokens(d?.cached_tokens) ?? 0;
  const cd = d?.cached_tokens_details;
  const ct = tokens(cd?.text_tokens) ?? (cached === 0 ? 0 : undefined),
    ci = tokens(cd?.image_tokens) ?? (cached === 0 ? 0 : undefined);
  if (
    (!record.provider || record.provider === "openai") &&
    input !== undefined &&
    text !== undefined &&
    image !== undefined &&
    text + image === input &&
    ct !== undefined &&
    ci !== undefined &&
    ct + ci === cached &&
    ct <= text &&
    ci <= image
  ) {
    cost.inputUsd = round(
      ((text - ct) * pricing.image.textInputPerMillion +
        (image - ci) * pricing.image.imageInputPerMillion +
        ct * pricing.image.cachedTextPerMillion +
        ci * pricing.image.cachedImagePerMillion) /
        1e6,
    );
    cost.cachedInputTokens = cached;
  } else cost.missingInput = 1;
  if (
    (!record.provider || record.provider === "openai") &&
    output !== undefined
  )
    cost.outputUsd = round((output * pricing.image.outputPerMillion) / 1e6);
  else cost.missingOutput = 1;
  cost.totalUsd = round(cost.inputUsd + cost.outputUsd);
  return cost;
}
