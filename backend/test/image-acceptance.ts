// Explicit paid integration check. Never executed by npm test.
import "dotenv/config";
import assert from "node:assert/strict";
import { cp, copyFile, readFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { native } from "../src/pipeline.js";
import {
  createProject,
  projectDir,
  getProject,
  saveProject,
  readJson,
  writeJson,
} from "../src/store.js";
import { prepareVisualPlan, applyVisuals } from "../src/visuals.js";
import { projectTokenUsage } from "../src/token-usage.js";
const sourceId = process.argv[2];
if (!sourceId) throw new Error("Usage: npx tsx test/image-acceptance.ts <completed-project-id> (paid image check)");
const original = await getProject(sourceId);
const p = await createProject("[Тест изображений] ИИ в образовании");
console.log("Diagnostic project", p.id);
p.revision = 1;
p.busy = true;
const folder = projectDir(p.id),
  assembled = join(folder, "assembled-r1");
await native(
  "assemble",
  join(projectDir(original.id), `assembled-r${original.revision}`),
  {
    layoutIds: [original.contract.slides[0].assembledLayoutId],
    output: assembled,
  },
);
await copyFile(
  join(assembled, "presentation.pptx"),
  join(assembled, "source.pptx"),
);
const analysis = await native("analyze", assembled);
await cp(assembled, join(folder, "source"), { recursive: true });
const sha = createHash("sha256")
  .update(await readFile(join(assembled, "source.pptx")))
  .digest("hex");
p.analysis = analysis;
p.brief = { ...original.brief, slideCount: 1 } as any;
p.research = original.research;
p.plan = {
  ...original.plan,
  slides: [
    { ...original.plan!.slides[0], sourceLayoutId: analysis.layouts[0].id },
  ],
} as any;
p.contract = {
  ...original.contract,
  revision: 1,
  sourceSha256: sha,
  assembledSha256: sha,
  plan: p.plan,
  slides: [
    {
      ...original.contract.slides[0],
      index: 0,
      assembledLayoutId: analysis.layouts[0].id,
      native: analysis.layouts[0],
    },
  ],
};
await saveProject(p);
await prepareVisualPlan(p, new AbortController().signal);
const choices = p.visuals!.choices;
const content = choices
  .filter((c) => !c.slot.protected && !c.slot.requiresOpaque)
  .sort((a, b) => b.slot.box.w * b.slot.box.h - a.slot.box.w * a.slot.box.h)[0];
assert.ok(content);
for (const c of choices) c.mode = "keep";
content.mode = "generate";
content.kind = "illustration";
content.background = "transparent";
content.quality = "medium";
await saveProject(p);
console.log(
  "Slots analyzed",
  choices.length,
  "generate one",
  content.slotIndex,
  "kind",
  content.kind,
);
const prior = await readJson(
  join(projectDir(original.id), `filled-r${original.revision}.json`),
);
const slide = structuredClone(prior.slides[0]);
slide.native.sourceSlideId = analysis.layouts[0].id;
slide.native.ordinal = 1;
slide.native.fields = Object.fromEntries(
  analysis.layouts[0].slots.map((s: any) => [
    s.key,
    slide.native.fields[s.key] ?? s.text,
  ]),
);
const images = await applyVisuals(p, [slide], new AbortController().signal);
console.log("Generation status", content.status, content.message || "");
assert.equal(Object.keys(images).length, 1);
const output = join(folder, "output-r1");
await mkdir(output, { recursive: true });
const result = await native("export", assembled, {
  slides: [slide],
  images,
  output,
});
assert.deepEqual(result.issues, []);
content.applied = true;
await writeJson(join(folder, "filled-r1.json"), {
  ...prior,
  assembledSha256: sha,
  slides: [slide],
});
await writeJson(join(folder, "template-r1.json"), p.contract);
p.busy = false;
p.status = "complete";
p.result = { slides: 1 };
delete p.progress;
await saveProject(
  p,
  "Проверка GPT Image 2: одна картинка с прозрачным фоном вставлена в исходный объект PPTX.",
);
const usage = await projectTokenUsage(folder);
console.log(
  JSON.stringify({
    projectId: p.id,
    output,
    asset: content.asset,
    cost: usage.cost,
    total: usage.total,
  }),
);
