import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  copyCacheFingerprint,
  legacyCopyFingerprints,
} from "../src/copy-cache.js";
const context = {
  contextVersion: 2,
  source: "facts",
  plan: { title: "approved" },
  sha: "pptx",
  spec: {
    native: {
      slots: [{ key: "title", x: 10, w: 200 }],
      visualSlots: [{ protected: false }],
    },
    semantics: {
      fields: [{ key: "title", role: "title" }],
      images: [{ role: "content" }],
    },
  },
};
test("image classification and native visual protection cannot invalidate checked copy", () => {
  const changed = structuredClone(context);
  changed.spec.semantics.images[0].role = "icon";
  changed.spec.native.visualSlots[0].protected = true;
  assert.equal(copyCacheFingerprint(context), copyCacheFingerprint(changed));
  for (const key of ["source", "sha"])
    assert.notEqual(
      copyCacheFingerprint(context),
      copyCacheFingerprint({ ...context, [key]: "changed" }),
    );
  changed.spec.native.slots[0].w = 100;
  assert.notEqual(copyCacheFingerprint(context), copyCacheFingerprint(changed));
});
test("v6 migration accepts only exact old context with saved image metadata", () => {
  const old = createHash("sha256")
    .update(JSON.stringify({ version: 6, ...context }))
    .digest("hex");
  const changed = structuredClone(context);
  changed.spec.semantics.images[0].role = "icon";
  assert.ok(
    legacyCopyFingerprints(changed, context.spec.semantics.images).includes(
      old,
    ),
  );
  assert.ok(
    !legacyCopyFingerprints(
      { ...changed, source: "other facts" },
      context.spec.semantics.images,
    ).includes(old),
  );
});
test('accepted advisory warnings survive a render pass but not data or policy changes',async()=>{
 const {acceptCopyWarnings,unresolvedCopyIssues}=await import('../src/copy-cache.js');
 const data={fields:{a:{text:'7%',evidence:[]}}}, issues=[{key:'a',reason:'unsupported_number',unsupportedNumbers:['7']}];
 const receipt=acceptCopyWarnings(data,issues,8);
 assert.deepEqual(unresolvedCopyIssues(issues,data,receipt,8),[]);
 assert.equal(unresolvedCopyIssues(issues,data,receipt,9).length,1);
 assert.equal(unresolvedCopyIssues(issues,{fields:{}},receipt,8).length,1);
 assert.equal(unresolvedCopyIssues([{key:'a',reason:'overflow'}],data,receipt,8).length,1);
 assert.equal(unresolvedCopyIssues([{...issues[0],unsupportedNumbers:['9']}],data,receipt,8).length,1);
});
