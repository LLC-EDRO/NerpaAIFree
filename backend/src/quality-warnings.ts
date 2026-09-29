import type { Issue } from './repair.js';

export type QualityWarning = { slide?: number; key?: string; reason: string; message: string };
export function qualityWarnings(issues: Issue[], slide?: number): QualityWarning[] {
  return issues.map(issue => ({
    slide: slide ?? (Number.isInteger(issue.slide) ? issue.slide! + 1 : undefined),
    key: issue.key,
    reason: issue.reason,
    message: warningMessage(issue),
  }));
}
function warningMessage(issue: Issue): string {
  if (issue.reason === 'source_slide_preserved') return 'Сохранён исходный макет: автоматическое заполнение не завершилось. Замените оставшиеся образцы текста вручную.';
  if (issue.reason === 'preview_unavailable') return 'PPTX создан, но PDF и предпросмотр недоступны. Откройте презентацию в PowerPoint.';
  if (issue.reason === 'quality_check_unavailable') return 'Не удалось завершить визуальную проверку. Проверьте расположение объектов.';
  if (issue.reason === 'rendered_text_outside_frame_visible') return 'Текст выходит за рамку поля, но остаётся видимым. При необходимости увеличьте поле или сократите текст.';
  if (issue.reason === 'evidence_not_in_source') return 'Не удалось сопоставить ссылку с сохранённым источником. Проверьте источник утверждения.';
  if (['unsupported_number','metric_fact_required','content_mismatch','invalid_evidence'].includes(issue.reason)) return 'Проверьте формулировку, числа и ссылки на источники.';
  if (issue.reason === 'overflow') return 'Проверьте текст: возможен выход за границы поля или пересечение с соседними объектами.';
  if (issue.reason === 'unverifiable' || issue.reason.startsWith('rendered_')) return 'Проверьте читаемость и расположение текста: автоматическая проверка оставила замечание.';
  if (issue.reason === 'repair_unavailable') return 'Автоисправление не завершилось. Сохранён доступный вариант слайда; проверьте его вручную.';
  return issue.message || 'Автоматическая проверка оставила замечание. Проверьте этот слайд вручную.';
}
export function uniqueWarnings(warnings: QualityWarning[]) {
  return [...new Map(warnings.map(w => [JSON.stringify([w.slide,w.key,w.reason,w.message]),w])).values()];
}
