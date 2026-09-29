import test from "node:test";
import assert from "node:assert/strict";
import {
  parseWebResearch,
  publicResearchUrl,
  researchStamp,
} from "../src/research.js";
import { projectFacts } from "../src/pipeline.js";
import { briefSchema, type Project } from "../src/domain.js";

const cited = "По данным опроса 2025 года, 42% опрошенных используют ИИ. [1]";
const payload = {
  status: "completed",
  output: [
    {
      type: "web_search_call",
      status: "completed",
      action: { queries: ["образование статистика"], sources: [] },
    },
    {
      type: "message",
      content: [
        {
          type: "output_text",
          text: cited + "\n\nНеподтверждённые 99% не должны стать фактом.",
          annotations: [
            {
              type: "url_citation",
              start_index: cited.length - 3,
              end_index: cited.length,
              url: "https://example.org/study?utm_source=test",
              title: "Исходный опрос",
            },
          ],
        },
      ],
    },
  ],
};
test("search admits only paragraphs with real provider citations and retains attribution", () => {
  const result = parseWebResearch(payload);
  assert.equal(result.evidence.length, 1);
  assert.match(result.evidence[0].text, /2025.*42% опрошенных/);
  assert.equal(result.sources[0].url, "https://example.org/study");
  assert.deepEqual(result.evidence[0].sourceIds, [result.sources[0].id]);
  assert.equal(result.status, "ready");
});
test("a printed URL and discovered source do not count as supported evidence", () => {
  const raw = structuredClone(payload);
  raw.output[1].content![0].annotations = [];
  const result = parseWebResearch(raw);
  assert.equal(result.status, "empty");
  assert.deepEqual(result.evidence, []);
  assert.throws(() => parseWebResearch({ ...payload, status: "incomplete" }));
  assert.throws(() =>
    parseWebResearch({ ...payload, output: [payload.output[1]] }),
  );
});
test("unsafe citation URLs and invalid annotation positions are rejected", () => {
  for (const url of [
    "javascript:alert(1)",
    "http://127.0.0.1/",
    "https://local.internal/",
    "https://user:secret@example.org/",
  ])
    assert.equal(publicResearchUrl(url), undefined);
  const raw = structuredClone(payload);
  raw.output[1].content![0].annotations[0].end_index = 999999;
  assert.deepEqual(parseWebResearch(raw).evidence, []);
});
test("research is excluded when disabled or stale and is used in both plan and fill facts", () => {
  const brief = briefSchema.parse({
    topic: "Образование с ИИ",
    count: 3,
    sourceText: "Больше статистики",
  });
  assert.equal(brief.webSearch, true);
  const p = {
    brief,
    research: {
      ...parseWebResearch(payload),
      stamp: researchStamp(brief),
      searchedAt: new Date().toISOString(),
      model: "test",
    },
  } as Project;
  assert.match(projectFacts(p), /42%/);
  p.brief!.webSearch = false;
  assert.doesNotMatch(projectFacts(p), /42%/);
  p.brief!.webSearch = true;
  p.brief!.topic = "Другая тема";
  assert.doesNotMatch(projectFacts(p), /42%/);
});

test("empty citations trigger targeted retries; facts merge with correct source IDs", async (t) => {
  const { searchResearch, mergeResearch, researchContext } =
    await import("../src/research.js");
  const { mkdtemp, rm, readdir } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const folder = await mkdtemp(join(tmpdir(), "nerpa-search-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const key = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test-key";
  t.after(() => {
    if (key === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = key;
  });
  let calls = 0;
  const requests: any[] = [];
  const fake: typeof fetch = async (_url, options) => {
    requests.push(JSON.parse(options!.body as string));
    const response = structuredClone(payload);
    if (++calls === 1) response.output[1].content![0].annotations = [];
    return new Response(JSON.stringify(response));
  };
  const brief = briefSchema.parse({ topic: "ИИ в образовании", count: 3 });
  const requirements = [{ slide: 1, intent: "Доля пользователей" }];
  const found = await searchResearch(
    brief,
    folder,
    new AbortController().signal,
    fake,
    undefined,
    requirements,
    1,
  );
  assert.equal(calls, 2);
  assert.equal(found.evidence.length, 1);
  assert.equal((await readdir(join(folder, "llm"))).length, 2);
  assert.deepEqual(JSON.parse(requests[1].input).requirements, requirements);
  assert.ok(JSON.parse(requests[1].input).retryFeedback);
  assert.equal(found.contextStamp, researchContext(undefined, requirements));
  assert.notEqual(
    found.contextStamp,
    researchContext(undefined, [{ intent: "Другой факт" }]),
  );
  const other = structuredClone(found);
  other.sources[0].url = "https://example.org/another";
  other.evidence[0].text = "Другой подтверждённый факт: 15 проектов.";
  const merged = mergeResearch(found, other);
  assert.equal(merged.evidence.length, 2);
  assert.deepEqual(merged.evidence[1].sourceIds, [merged.sources[1].id]);
  assert.equal(mergeResearch(merged, other).evidence.length, 2);
});

test("search stops after three empty passes without accepting bare links as numeric facts", async (t) => {
  const { searchResearch } = await import("../src/research.js");
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const folder = await mkdtemp(join(tmpdir(), "nerpa-empty-search-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const oldKey = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test-key";
  t.after(() => {
    if (oldKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = oldKey;
  });
  let calls = 0;
  const fake: typeof fetch = async () => {
    calls++;
    const raw = structuredClone(payload);
    raw.output[1].content![0].annotations = [];
    return new Response(JSON.stringify(raw));
  };
  const result = await searchResearch(
    briefSchema.parse({ topic: "Метрики ИИ", count: 3 }),
    folder,
    new AbortController().signal,
    fake,
    undefined,
    [],
    3,
  );
  assert.equal(calls, 3);
  assert.equal(result.status, "empty");
  assert.deepEqual(result.evidence, []);
});

test("incremental search carries cited facts and focuses on changed slide topics", async (t) => {
  const { searchResearch } = await import("../src/research.js");
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const folder = await mkdtemp(join(tmpdir(), "nerpa-search-focus-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const oldKey = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test-key";
  t.after(() => {
    if (oldKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = oldKey;
  });
  const brief = briefSchema.parse({
    topic: "Образование и энергетика",
    count: 2,
  });
  const unchanged = {
    title: "Образование",
    brief: "Доля пользователей ИИ",
    sourceLayoutId: "a",
  };
  const added = {
    title: "Энергетика",
    brief: "Новые мощности",
    sourceLayoutId: "b",
  };
  const existing = {
    ...parseWebResearch(payload),
    stamp: researchStamp(brief),
    searchedAt: new Date().toISOString(),
    model: "test",
    searchedOutline: [
      JSON.stringify({ title: unchanged.title, brief: unchanged.brief }),
    ],
    searchedRequirements: [JSON.stringify({ intent: "Доля пользователей" })],
  };
  let request: any;
  const fake: typeof fetch = async (_url, options) => {
    request = JSON.parse(JSON.parse(options!.body as string).input);
    return new Response(JSON.stringify(payload));
  };
  const result = await searchResearch(
    brief,
    folder,
    new AbortController().signal,
    fake,
    { slides: [unchanged, added] },
    [{ intent: "Доля пользователей" }, { intent: "Мощности" }],
    1,
    existing,
  );
  assert.deepEqual(request.approvedOutline, [
    { title: added.title, brief: added.brief },
  ]);
  assert.deepEqual(request.requirements, [{ intent: "Мощности" }]);
  assert.equal(request.existingFacts[0].text, existing.evidence[0].text);
  assert.equal(result.evidence.length, 1);
  assert.equal(result.searchedOutline?.length, 2);
});

test("incomplete search retries within search budget and retains cited results", async (t) => {
  const { searchResearch } = await import("../src/research.js");
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  const folder = await mkdtemp(join(tmpdir(), "nerpa-search-recovery-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const key = process.env.OPENAI_API_KEY;
  process.env.OPENAI_API_KEY = "test";
  t.after(() => {
    if (key === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = key;
  });
  let calls = 0;
  const result = await searchResearch(
    briefSchema.parse({ topic: "ИИ в образовании", count: 3, sourceText: "" }),
    folder,
    new AbortController().signal,
    (async () => {
      calls++;
      return new Response(
        JSON.stringify(
          calls === 1 ? { status: "incomplete", output: [] } : payload,
        ),
      );
    }) as typeof fetch,
  );
  assert.equal(calls, 2);
  assert.equal(result.evidence.length, 1);
});

test("bounded searches with scarce or empty facts are cached; changed context is searched again", async () => {
  const { reusableResearch, researchContext } =
    await import("../src/research.js");
  const brief = briefSchema.parse({ topic: "Метрики образования", count: 3 });
  const context = researchContext();
  const saved = {
    ...parseWebResearch(payload),
    stamp: researchStamp(brief),
    contextStamp: context,
    searchedAt: new Date().toISOString(),
    model: "test",
    coverage: {
      targetNumericFacts: 8,
      foundNumericFacts: 1,
      sufficient: false,
    },
  };
  assert.equal(reusableResearch(saved, brief, context), true);
  assert.equal(
    reusableResearch(
      { ...saved, evidence: [], status: "empty" },
      brief,
      context,
    ),
    true,
  );
  assert.equal(reusableResearch(saved, brief, "new-context"), false);
  assert.equal(
    reusableResearch(saved, { ...brief, topic: "Другая тема" }, context),
    false,
  );
});
