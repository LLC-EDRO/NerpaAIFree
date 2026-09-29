import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  normalizeUsage,
  summarizeUsage,
  projectTokenUsage,
} from "../src/token-usage.js";

test("automatic plan adaptation is counted in the planning stage", () => {
  const result = summarizeUsage([normalizeUsage({stage:"plan-adapt-1",usage:{prompt_tokens:120,completion_tokens:30}})]);
  assert.equal(result.stages.find((s)=>s.id==="plan")?.inputTokens,120);
  assert.equal(result.stages.find((s)=>s.id==="plan")?.outputTokens,30);
  assert.equal(result.total.requests,1);
});

test("chat and search usage include cache/reasoning once, retries count separately", () => {
  const records = [
    {
      stage: "fill-1-0",
      usage: {
        prompt_tokens: 100,
        completion_tokens: 40,
        total_tokens: 140,
        prompt_tokens_details: { cached_tokens: 80 },
        completion_tokens_details: { reasoning_tokens: 30 },
      },
    },
    {
      stage: "fill-1-0",
      attempt: 1,
      usage: { prompt_tokens: 120, completion_tokens: 25 },
    },
    {
      stage: "web-search",
      response: { usage: { input_tokens: 1000, output_tokens: 300 } },
    },
    {
      stage: "content-review-1-0",
      usage: { prompt_tokens: 50, completion_tokens: 10 },
    },
  ];
  const result = summarizeUsage(records.map(normalizeUsage));
  assert.deepEqual(result.total, {
    inputTokens: 1270,
    outputTokens: 375,
    requests: 4,
    missingInput: 0,
    missingOutput: 0,
  });
  assert.equal(result.stages.find((s) => s.id === "fill")?.inputTokens, 220);
  assert.equal(result.stages.find((s) => s.id === "search")?.outputTokens, 300);
  assert.equal(result.stages.find((s) => s.id === "review")?.inputTokens, 50);
  assert.equal(result.stages.find((s) => s.id === "export")?.requests, 0);
});
test("unknown or partially reported usage is not presented as known zero", () => {
  const result = summarizeUsage([
    normalizeUsage({ stage: "plan", usage: { prompt_tokens: 0 } }),
    normalizeUsage({
      stage: "describe-1",
      usage: { input_tokens: -1, output_tokens: "12" },
    }),
    normalizeUsage({
      stage: "new-provider-step",
      usage: { input_tokens: 10, output_tokens: 2 },
    }),
  ]);
  assert.equal(result.total.missingInput, 1);
  assert.equal(result.total.missingOutput, 2);
  assert.equal(result.stages.find((s) => s.id === "plan")?.missingInput, 0);
  assert.equal(result.stages.find((s) => s.id === "other")?.inputTokens, 10);
});
test("historical project receipts survive repeated reads, new calls and edited logs without duplication", async (t) => {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-usage-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  assert.equal((await projectTokenUsage(folder)).total.requests, 0);
  await mkdir(join(folder, "llm"));
  const log = join(folder, "llm", "old.json");
  await writeFile(
    log,
    JSON.stringify({
      stage: "plan",
      usage: { prompt_tokens: 10, completion_tokens: 2 },
    }),
  );
  await writeFile(join(folder, "llm", "pending.tmp"), "ignored");
  const first = await projectTokenUsage(folder);
  assert.deepEqual(await projectTokenUsage(folder), first);
  await writeFile(
    join(folder, "llm", "retry.json"),
    JSON.stringify({
      stage: "plan",
      usage: { prompt_tokens: 20, completion_tokens: 5 },
    }),
  );
  assert.equal((await projectTokenUsage(folder)).total.inputTokens, 30);
  await writeFile(
    log,
    JSON.stringify({
      stage: "plan",
      usage: { prompt_tokens: 100, completion_tokens: 20 },
    }),
  );
  assert.equal((await projectTokenUsage(folder)).total.inputTokens, 120);
  await writeFile(join(folder, "llm", "broken.json"), "{");
  const damaged = await projectTokenUsage(folder);
  assert.equal(damaged.total.requests, 3);
  assert.equal(damaged.total.missingInput, 1);
  assert.equal(damaged.total.inputTokens, 120);
  assert.deepEqual(await projectTokenUsage(folder), damaged);
});
