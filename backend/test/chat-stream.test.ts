import test from "node:test";
import assert from "node:assert/strict";
import { readChatCompletion } from "../src/chat-stream.js";
import { providerJson } from "../src/provider-request.js";

function stream(text: string) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    // Includes splits inside UTF-8 characters, CRLF and event delimiters.
    for (let i = 0; i < bytes.length; i += 3) controller.enqueue(bytes.slice(i, i + 3));
    controller.close();
  } }), { headers: { "content-type": "text/event-stream; charset=utf-8" } });
}
const frame = (delta: any, finish_reason: string | null = null) =>
  `data: ${JSON.stringify({ model: "local", choices: [{ index: 0, delta, finish_reason }] })}\r\n\r\n`;

test("SSE preserves content, usage, finish and refusal without reasoning", async () => {
  const result = await readChatCompletion(stream(": heartbeat\r\n\r\n" +
    frame({ reasoning_content: "private thought" }) + frame({ content: '{"title":"' }) +
    frame({ content: 'Привет"}' }) + frame({}, "stop") +
    'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":9}}\r\n\r\n' +
    'data: [DONE]\r\n\r\n'));
  assert.deepEqual(JSON.parse(result.choices[0].message.content), { title: "Привет" });
  assert.deepEqual(result.usage, { prompt_tokens: 12, completion_tokens: 9 });
  assert.equal(result.choices[0].finish_reason, "stop");
  assert.ok(!JSON.stringify(result).includes("private thought"));
  const refusal = await readChatCompletion(stream(frame({ refusal: "no" }, "content_filter")));
  assert.equal(refusal.choices[0].message.refusal, "no");
  assert.equal(refusal.choices[0].finish_reason, "content_filter");
});

test("SSE truncation stays visible, broken transport retries instead of accepting partial JSON", async () => {
  const truncated = await readChatCompletion(stream(frame({ content: '{"a":' }, "length")));
  assert.equal(truncated.choices[0].finish_reason, "length");
  await assert.rejects(() => readChatCompletion(stream(frame({ content: '{}' }))));
  await assert.rejects(() => readChatCompletion(stream('data: {"error":"failure"}\n\ndata: [DONE]\n\n')));
  let calls = 0;
  const result = await providerJson({ url: "https://local.invalid", init: {},
    model: "local", prefix: "llm", timeoutMs: 1000, delayMs: 0,
    decode: readChatCompletion, fetcher: async () => ++calls === 1
      ? stream(frame({ content: '{"broken":' }))
      : stream(frame({ content: '{"ok":true}' }, "stop")),
  });
  assert.equal(calls, 2);
  assert.deepEqual(JSON.parse(result.choices[0].message.content), { ok: true });
});

test("model override to nonstreaming JSON remains compatible", async () => {
  const value = { choices: [{ message: { content: "{}" } }], usage: { prompt_tokens: 1 } };
  assert.deepEqual(await readChatCompletion(new Response(JSON.stringify(value))), value);
});
