import type { Issue } from "./repair.js";

/** Picture geometry is repaired independently from already accepted copy. */
export function imageOnlyIssue(issue: Issue) {
  return (
    !!issue.details?.length &&
    issue.details.every((d) =>
      [
        "generated_photo_no_safe_region",
        "generated_photo_overlaps_text",
        "generated_photos_overlap",
      ].includes(d),
    )
  );
}

/** Remove only unsafe replacements. The native exporter then keeps originals.
 * A raster occlusion does not identify its picture, so retry all replacements
 * on that slide once; any remaining text error still goes to text repair. */
export function restoreUnsafeImages(slides: any[], issues: Issue[]) {
  const removed: { slideIndex: number; shapeId: number }[] = [];
  for (const issue of issues) {
    if (!Number.isInteger(issue.slide)) continue;
    const slide = slides[issue.slide!];
    if (!slide?.images?.length) continue;
    const rasterConflict = issue.details?.some((d) =>
      ["rendered_text_occluded", "rendered_text_indistinguishable"].includes(d),
    );
    if (!imageOnlyIssue(issue) && !rasterConflict) continue;
    slide.images = slide.images.filter((image: any) => {
      if (!rasterConflict && issue.imageShapeId !== image.shapeId) return true;
      removed.push({ slideIndex: issue.slide!, shapeId: image.shapeId });
      return false;
    });
  }
  return removed;
}
