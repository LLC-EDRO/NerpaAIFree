import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { nativeFitVersion } from "../src/repair.js";
test("server and native fitter versions agree so obsolete fit rejects are released", async () => {
  const runtime = await readFile(
    new URL(
      "../vendor/presentation-agent/runtime/text_frames.py",
      import.meta.url,
    ),
    "utf8",
  );
  assert.equal(
    Number(runtime.match(/^FIT_VERSION = (\d+)$/m)?.[1]),
    nativeFitVersion,
  );
});
