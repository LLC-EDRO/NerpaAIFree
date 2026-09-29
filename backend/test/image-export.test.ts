import test from "node:test";
import assert from "node:assert/strict";
import { imageOnlyIssue, restoreUnsafeImages } from "../src/image-export.js";

test("picture overlap restores only the reported image without touching copy", () => {
  const slides = [
    {
      native: { fields: { title: "Checked text" } },
      images: [{ shapeId: 11 }, { shapeId: 42 }],
    },
  ];
  const issue = {
    slide: 0,
    key: "title",
    imageShapeId: 42,
    reason: "overlap",
    details: ["generated_photo_overlaps_text"],
  };
  assert.equal(imageOnlyIssue(issue), true);
  assert.deepEqual(restoreUnsafeImages(slides, [issue, issue]), [
    { slideIndex: 0, shapeId: 42 },
  ]);
  assert.deepEqual(slides[0].images, [{ shapeId: 11 }]);
  assert.equal(slides[0].native.fields.title, "Checked text");
});

test("raster occlusion retries original images once; genuine text overflow is retained", () => {
  const slides = [{ images: [{ shapeId: 5 }] }, { images: [{ shapeId: 6 }] }];
  const issues = [
    { slide: 0, reason: "overflow", details: ["rendered_text_occluded"] },
    { slide: 1, reason: "overflow", details: ["source_text_frame_overflow"] },
  ];
  assert.deepEqual(restoreUnsafeImages(slides, issues), [
    { slideIndex: 0, shapeId: 5 },
  ]);
  assert.deepEqual(restoreUnsafeImages(slides, issues), []);
  assert.deepEqual(slides[1].images, [{ shapeId: 6 }]);
  assert.equal(imageOnlyIssue(issues[1]), false);
  assert.equal(
    imageOnlyIssue({
      reason: "overflow",
      details: ["generated_photo_overlaps_text", "source_text_frame_overflow"],
    }),
    false,
  );
});
