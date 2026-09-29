import test from "node:test";
import assert from "node:assert/strict";
import { receiptCost } from "../src/usage-cost.js";
import {
  normalizeUsage,
  summarizeUsage,
  projectTokenUsage,
} from "../src/token-usage.js";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const record = (input = 100000, output = 10000, cached = 0, written = 0) => ({
  model: "gpt-5.6-luna",
  stage: "fill-1-0",
  usage: {
    prompt_tokens: input,
    completion_tokens: output,
    prompt_tokens_details: {
      cached_tokens: cached,
      cache_write_tokens: written,
    },
    completion_tokens_details: { reasoning_tokens: output / 2 },
  },
});
test("Luna prices uncached, cached, cache writes and reasoning exactly once", () => {
  const cost = receiptCost(record(100000, 10000, 20000, 30000));
  assert.equal(cost.inputUsd, 0.0179);
  assert.equal(cost.outputUsd, 0.012);
  assert.equal(cost.totalUsd, 0.0299);
  assert.equal(cost.missingInput + cost.missingOutput + cost.missingSearch, 0);
});
test("long-context rate applies per request, not the project aggregate", () => {
  assert.equal(receiptCost(record(272000)).inputUsd, 0.0544);
  const long = receiptCost(record(300000));
  assert.equal(long.inputUsd, 0.12);
  assert.equal(long.outputUsd, 0.018);
  const summary = summarizeUsage(
    [record(), record(), record()].map(normalizeUsage),
  );
  assert.equal(summary.cost.inputUsd, 0.06);
  assert.equal(summary.cost.outputUsd, 0.036);
  assert.equal(
    summary.stages.find((x) => x.id === "fill")?.cost.totalUsd,
    0.096,
  );
});
test("search counts actual tool invocations and token costs once", () => {
  const cost = receiptCost({
    model: "gpt-5.6-luna",
    stage: "web-search",
    response: {
      model: "gpt-5.6-luna",
      service_tier: "default",
      usage: {
        input_tokens: 10000,
        output_tokens: 2000,
        input_tokens_details: { cached_tokens: 2000 },
      },
      output: [
        {
          type: "web_search_call",
          id: "a",
          action: {
            type: "search",
            queries: ["one", "two"],
            sources: [1, 2, 3],
          },
        },
        { type: "web_search_call", id: "b", action: { type: "open_page" } },
        { type: "web_search_call", id: "a" },
        { type: "message", content: [{ annotations: [1, 2, 3, 4] }] },
      ],
    },
  });
  assert.equal(cost.searchCalls, 2);
  assert.equal(cost.searchUsd, 0.02);
  assert.equal(cost.totalUsd, 0.02404);
});
test("unsupported models/providers/tiers and malformed usage remain unknown", () => {
  for (const override of [
    { model: "deepseek-chat" },
    { responseModel: "unknown" },
    { provider: "deepseek" },
    { serviceTier: "unknown" },
  ]) {
    const cost = receiptCost({ ...record(), ...override });
    assert.equal(cost.missingInput, 1);
    assert.equal(cost.missingOutput, 1);
    assert.equal(cost.totalUsd, 0);
  }
  for (const r of [
    null,
    { stage: "web-search" },
    { ...record(), usage: {} },
    record(100, 20, 80, 50),
  ])
    assert.equal(receiptCost(r).missingInput, 1);
  assert.equal(receiptCost({ stage: "web-search" }).missingSearch, 1);
  assert.equal(
    receiptCost({
      ...record(),
      usage: { prompt_tokens: 100, completion_tokens: 20 },
    }).missingInput,
    1,
  );
  assert.equal(receiptCost(record(0, 0)).totalUsd, 0);
  assert.equal(receiptCost(record(0, 0)).missingInput, 0);
});
test("response model and returned service tier control prices", () => {
  assert.equal(
    receiptCost({ ...record(), serviceTier: "fast" }).totalUsd,
    0.064,
  );
  assert.equal(
    receiptCost({ ...record(), serviceTier: "priority" }).totalUsd,
    0.064,
  );
  assert.equal(
    receiptCost({ ...record(), serviceTier: "flex" }).totalUsd,
    0.016,
  );
  assert.equal(
    receiptCost({
      ...record(),
      model: "unknown",
      responseModel: "gpt-5.6-luna",
    }).totalUsd,
    0.032,
  );
});
test("receipt cache backfills history without duplicate charges; retry adds cost", async (t) => {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-cost-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  assert.equal((await projectTokenUsage(folder)).cost.totalUsd, 0);
  await mkdir(join(folder, "llm"));
  await writeFile(join(folder, "llm", "old.json"), JSON.stringify(record()));
  const first = await projectTokenUsage(folder);
  assert.equal(first.cost.totalUsd, 0.032);
  assert.deepEqual(await projectTokenUsage(folder), first);
  await writeFile(join(folder, "llm", "retry.json"), JSON.stringify(record()));
  assert.equal((await projectTokenUsage(folder)).cost.totalUsd, 0.064);
  await writeFile(join(folder, "llm", "broken.json"), "{");
  const partial = await projectTokenUsage(folder);
  assert.equal(partial.cost.totalUsd, 0.064);
  assert.equal(partial.cost.missingInput, 1);
  assert.equal(partial.cost.missingSearch, 1);
});

test("GPT-6 Luna pricing preserves GPT-5.6 receipts in mixed-model projects", () => {
  const old = record(100000, 10000, 20000, 30000);
  const fresh = {...old, model:"gpt-6-luna"};
  const cost = receiptCost(fresh);
  assert.equal(cost.inputUsd, 0.00895);
  assert.equal(cost.outputUsd, 0.005);
  assert.equal(cost.totalUsd, 0.01395);
  assert.equal(receiptCost(old).totalUsd, 0.0299);
  assert.equal(summarizeUsage([old,fresh].map(normalizeUsage)).cost.totalUsd, 0.04385);
  assert.equal(receiptCost({...record(300000),model:"gpt-6-luna"}).totalUsd, 0.0675);
  assert.equal(receiptCost({...fresh,serviceTier:"fast"}).totalUsd,0.0279);
  assert.equal(receiptCost({...fresh,serviceTier:"flex"}).totalUsd,0.006975);
});
