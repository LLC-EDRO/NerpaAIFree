/** GPT Image 2 custom sizes: multiples of 16, >=655360 pixels, ratio <=3.
 * Keep every request within a 1280x720 (or rotated) envelope. Tiny slots use
 * the smallest compatible API canvas, then a smaller delivery file. */
export function presentationImageSize(
  box?: { w: number; h: number },
  canvas?: { width: number; height: number },
) {
  if (
    !box ||
    !canvas ||
    ![box.w, box.h, canvas.width, canvas.height].every(
      (v) => Number.isFinite(v) && v > 0,
    )
  )
    return {
      width: 1280,
      height: 720,
      deliveryWidth: 1280,
      deliveryHeight: 720,
    };
  const scale = Math.min(1280 / canvas.width, 720 / canvas.height);
  const deliveryWidth = Math.max(1, Math.min(1280, Math.ceil(box.w * scale)));
  const deliveryHeight = Math.max(1, Math.min(720, Math.ceil(box.h * scale)));
  const portrait = box.h > box.w;
  const ratio = Math.min(
    3,
    Math.max(1, portrait ? box.h / box.w : box.w / box.h),
  );
  const targetPixels = Math.min(
    1280 * 720,
    Math.max(655360, deliveryWidth * deliveryHeight),
  );
  const candidates: Array<{ w: number; h: number; score: number }> = [];
  for (let w = 256; w <= 1280; w += 16)
    for (let h = 256; h <= 720; h += 16) {
      const pixels = w * h;
      if (w < h || w / h > 3 || pixels < 655360 || pixels > 1280 * 720)
        continue;
      // Match aspect first, while staying near the actually needed pixel budget.
      const score =
        Math.abs(Math.log(w / h / ratio)) * 3 +
        Math.abs(Math.log(pixels / targetPixels));
      candidates.push({ w, h, score });
    }
  candidates.sort((a, b) => a.score - b.score || a.w * a.h - b.w * b.h);
  const { w, h } = candidates[0]!;
  return {
    width: portrait ? h : w,
    height: portrait ? w : h,
    deliveryWidth,
    deliveryHeight,
  };
}
