import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { labAuthorEnvironment } from "../src/lab-environment.js";
import { withEngineContext } from "../src/runtime-context.js";
import { defaultSelection, withModelSelection } from "../src/model-settings.js";
import { llmJson } from "../src/llm.js";

test("lab default routes plan to DeepSeek and slide work to Luna", async (t) => {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-routing-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const env = {
    ...labAuthorEnvironment({}),
    DEEPSEEK_API_KEY: "test-deepseek",
    OPENAI_API_KEY: "test-openai",
  };
  assert.equal(env.OUTLINE_PROVIDER, "deepseek");
  assert.equal(env.DEEPSEEK_MODEL, "deepseek-flash");
  const calls: Array<{ url: string; body: any }> = [];
  const invoke = (stage: string, useVisionModel = false) =>
    llmJson({
      folder,
      stage,
      prompt: "Return JSON",
      payload: {},
      useVisionModel,
      validate: (value) => value,
      fetcher: (async (url, init) => {
        calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
        return new Response(JSON.stringify({ choices: [{ message: { content: "{}" }, finish_reason: "stop" }] }));
      }) as typeof fetch,
    });
  await withEngineContext({ workspace: folder, env }, () =>
    withModelSelection(defaultSelection(), async () => {
      await invoke("plan");
      await invoke("fill-slide-0");
      await invoke("describe-slide-0", true);
    }),
  );
  assert.match(calls[0].url, /api\.deepseek\.com/);
  assert.equal(calls[0].body.model, "deepseek-flash");
  assert.deepEqual(calls[0].body.thinking, { type: "disabled" });
  for (const call of calls.slice(1)) {
    assert.equal(call.url, "https://api.openai.com/v1/chat/completions");
    assert.equal(call.body.model, "gpt-6-luna");
  }
});
