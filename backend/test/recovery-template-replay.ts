// Read-only against project artifacts: analyze fresh private copies of uploads.
import "dotenv/config";
import { copyFile, mkdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { native } from "../src/pipeline.js";
import { writeJson } from "../src/store.js";
const root = resolve("data/validation/automatic-recovery");
const records: any[] = [];
for (const id of process.argv.slice(2)) {
  assert.match(id, /^[a-f0-9-]{36}$/);
  const source = resolve("data/projects", id, "source"),
    folder = join(root, id);
  const previous = JSON.parse(
    await readFile(join(source, "analysis.json"), "utf8"),
  );
  const bytes = await readFile(join(source, "source.pptx"));
  const sha = createHash("sha256").update(bytes).digest("hex");
  await mkdir(folder, { recursive: true });
  await copyFile(join(source, "source.pptx"), join(folder, "source.pptx"));
  console.log("Analyze", id, previous.layouts.length, "slides");
  const result = await native("analyze", folder);
  const oldUsable = new Set(
    previous.layouts.filter((l: any) => l.usable).map((l: any) => l.id),
  );
  const nowUsable = new Set(
    result.layouts.filter((l: any) => l.usable).map((l: any) => l.id),
  );
  assert.ok(
    [...oldUsable].every((id) => nowUsable.has(id)),
    "must not disable previously usable layouts",
  );
  assert.equal(result.sha256, sha);
  assert.equal(
    createHash("sha256")
      .update(await readFile(join(source, "source.pptx")))
      .digest("hex"),
    sha,
  );
  const row = {
    id,
    before: oldUsable.size,
    after: nowUsable.size,
    total: result.layouts.length,
    recovered: result.layouts
      .filter((l: any) => l.usable && !oldUsable.has(l.id))
      .map((l: any) => l.id),
    remaining: result.layouts
      .filter((l: any) => !l.usable)
      .map((l: any) => ({
        id: l.id,
        warnings: l.warnings,
        issues: l.preflightIssues,
      })),
    bulletFontRepairs: result.bulletFontSubstitutions.length,
    textboxRepairs: result.textboxRepairs,
    sourceSha256: sha,
  };
  records.push(row);
  await writeJson(join(root, "report.json"), records);
  console.log(JSON.stringify(row));
}
