import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, readdir, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { providerJson } from "../src/provider-request.js";
import { llmJson } from "../src/llm.js";

const response = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status });
const request = (fetcher: typeof fetch, signal?: AbortSignal) =>
  providerJson({
    url: "https://provider.invalid",
    init: {},
    timeoutMs: 1000,
    prefix: "llm",
    model: "test",
    delayMs: 0,
    fetcher,
    signal,
  });

test("delivery recovers from network, throttling and malformed response with a bounded budget", async () => {
  let calls = 0;
  assert.deepEqual(
    await request((async () => {
      calls++;
      if (calls === 1) throw new TypeError("network");
      if (calls === 2)
        return response(429, { error: { code: "rate_limit_exceeded" } });
      return response(200, { ok: true });
    }) as typeof fetch),
    { ok: true },
  );
  assert.equal(calls, 3);
  calls = 0;
  assert.deepEqual(
    await request((async () =>
      ++calls === 1
        ? new Response("broken json")
        : response(200, { ok: true })) as typeof fetch),
    { ok: true },
  );
  assert.equal(calls, 2);
});

test("credentials, quota and permanent errors do not retry; outages stop after three requests", async () => {
  for (const [status, code, expected] of [
    [401, "invalid_api_key", 1],
    [400, "invalid_request", 1],
    [429, "insufficient_quota", 1],
    [503, "unavailable", 3],
  ] as const) {
    let calls = 0;
    await assert.rejects(
      request((async () => {
        calls++;
        return response(status, { error: { code } });
      }) as typeof fetch),
      (e: any) => e.code === "llm_http" && e.details.status === status,
    );
    assert.equal(calls, expected);
  }
});

test("cancellation interrupts delivery instead of launching another request", async () => {
  const c = new AbortController();
  let calls = 0;
  await assert.rejects(
    request(
      (async () => {
        calls++;
        c.abort();
        throw new TypeError("network");
      }) as typeof fetch,
      c.signal,
    ),
    (e: any) => e.name === "AbortError",
  );
  assert.equal(calls, 1);
});

async function fixture(t: any) {
  const folder = await mkdtemp(join(tmpdir(), "nerpa-provider-test-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const key = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test-key";
  t.after(() => {
    if (key === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = key;
  });
  return folder;
}
const completion = (text: string, finish = "stop") =>
  response(200, {
    choices: [{ finish_reason: finish, message: { content: text } }],
    usage: { prompt_tokens: 10, completion_tokens: 5 },
  });

test("truncated JSON retries compactly, then repairs schema; every completion retains usage", async (t) => {
  const folder = await fixture(t),
    bodies: any[] = [];
  const result = await llmJson({
    folder,
    stage: "test",
    prompt: "Return JSON",
    payload: { task: "x" },
    useVisionModel: true,
    fetcher: (async (_url, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      return bodies.length === 1
        ? completion("partial ".repeat(1000), "length")
        : bodies.length === 2
          ? completion('{"wrong":true}')
          : completion('{"ok":true}');
    }) as typeof fetch,
    validate: (v: any) => {
      if (!v.ok) throw new Error("ok required");
      return v;
    },
  });
  assert.deepEqual(result, { ok: true });
  assert.equal(bodies.length, 3);
  assert.equal(
    bodies[1].max_completion_tokens,
    bodies[0].max_completion_tokens * 2,
  );
  assert.ok(!JSON.stringify(bodies[1]).includes("partial"));
  const records = await readdir(join(folder, "llm"));
  assert.equal(records.length, 3);
  for (const name of records)
    assert.equal(
      JSON.parse(await readFile(join(folder, "llm", name), "utf8")).usage
        .prompt_tokens,
      10,
    );
});

test("repeated truncation stops; refusal is never repaired into compliance", async (t) => {
  const folder = await fixture(t);
  let calls = 0;
  const input = {
    folder,
    stage: "test",
    prompt: "x",
    payload: {},
    useVisionModel: true,
    validate: (v: any) => v,
  };
  await assert.rejects(
    llmJson({
      ...input,
      fetcher: (async () => {
        calls++;
        return completion("partial", "length");
      }) as typeof fetch,
    }),
    (e: any) => e.code === "llm_truncated",
  );
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    llmJson({
      ...input,
      fetcher: (async () => {
        calls++;
        return response(200, {
          choices: [
            { message: { refusal: "Cannot comply" }, finish_reason: "stop" },
          ],
        });
      }) as typeof fetch,
    }),
    (e: any) => e.code === "llm_refusal",
  );
  assert.equal(calls, 1);
});
test('OpenAI requests carry the supplied strict JSON schema while validation still runs',async(t)=>{
 const folder=await fixture(t);let body:any;let validated=false;
 const schema={type:'object',additionalProperties:false,required:['ok'],properties:{ok:{type:'boolean'}}};
 const result=await llmJson({folder,stage:'test-schema',prompt:'Return JSON',payload:{},useVisionModel:true,schema,
  fetcher:(async(_url,init)=>{body=JSON.parse(String(init?.body));return completion('{"ok":true}');}) as typeof fetch,
  validate:(v:any)=>{validated=true;return v;},
 });
 assert.equal(body.response_format.type,'json_schema');assert.equal(body.response_format.json_schema.strict,true);assert.deepEqual(body.response_format.json_schema.schema,schema);assert.ok(validated&&result.ok);
});

test("GPT-6 Luna defaults route text and vision with low reasoning and repairs with medium", async (t) => {
  const folder = await fixture(t);
  const names = ['OPENAI_MODEL_MAIN','VISION_MODEL','LLM_PROVIDER','OPENAI_SEARCH_MODEL'];
  const previous = Object.fromEntries(names.map(n=>[n,process.env[n]]));
  names.forEach(n=>delete process.env[n]);
  t.after(()=>names.forEach(n=>{if(previous[n]===undefined)delete process.env[n];else process.env[n]=previous[n];}));
  const {modelInfo}=await import('../src/llm.js');
  assert.equal(modelInfo().textModel,'gpt-6-luna');
  assert.equal(modelInfo().visionModel,'gpt-6-luna');
  assert.equal(modelInfo().searchModel,'gpt-6-luna');
  for(const [options,effort] of [[{},'low'],[{repair:{issues:['overflow']}},'medium'],[{useVisionModel:true},'low'],[{reasoning:'medium' as const},'medium']] as const){
    let body:any;
    await llmJson({folder,stage:'migration-test',prompt:'Return JSON',payload:{},...options,
      fetcher:(async(_url,init)=>{body=JSON.parse(String(init?.body));return completion('{"ok":true}');}) as typeof fetch,
      validate:(v)=>v});
    assert.equal(body.model,'gpt-6-luna');assert.equal(body.reasoning_effort,effort);
    assert.equal(body.temperature,undefined);assert.equal(body.top_p,undefined);assert.equal(body.tools,undefined);
  }
});
