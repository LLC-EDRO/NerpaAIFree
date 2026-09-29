import { z } from 'zod';

export const alignmentSchema = z.object({
  horizontal: z.enum(['preserve','center']).default('preserve'),
  vertical: z.enum(['preserve','center']).default('preserve'),
  reason: z.string().min(5).max(300),
});
export const alignmentPrompt = `Оцени выравнивание текста внутри уже существующей рамки. Для отдельного короткого заголовка, числа, подписи шага или короткой надписи в высокой карточке/круге уместна vertical:center; horizontal:center выбирай только если вся композиция этого блока требует центровки. Это независимые оси: вертикальная центровка НЕ требует горизонтальной. Для обычных абзацев, списков, колонок и связанных отдельных полей число+подпись сохраняй исходное выравнивание. Не центрируй всё подряд. При уверенном решении добавь alignment:{horizontal:"center"|"preserve",vertical:"center"|"preserve",reason:"краткое обоснование по изображению"}; иначе опусти alignment. Для одиночной надписи в цветной карточке сервер может подогнать рамку по границам карточки; саму карточку он не передвигает. Сохраняй исходную вертикальную центровку при восстановлении.`;

/** Cosmetic decisions are advisory. Unknown, protected, linked and dense fields
 * simply retain the template; they must never trigger another paid retry. */
export function selectedAlignments(fields: any[], slots: any[]) {
  const groups = new Map<string, number>();
  for (const f of fields) groups.set(f.group, (groups.get(f.group)||0)+1);
  return Object.fromEntries(fields.flatMap(f => {
    const a=alignmentSchema.safeParse(f.alignment),s=slots.find(s=>s.key===f.key);
    if (!a.success || !s || s.cell || f.action!=='replace' || !['title','metric','metric_label','step_number','body'].includes(f.role) || (groups.get(f.group)!==1 && fields.some(peer=>peer.group===f.group && ['metric','metric_label','unit'].includes(peer.role)))) return [];
    return [[f.key,a.data]];
  }));
}

export function alignmentIntent(alignment: unknown, slot: any, text: string) {
  const parsed=alignmentSchema.safeParse(alignment);
  if (!parsed.success || slot.cell || text.trim().length>160 || text.trim().split('\n').length>3 || /^\s*[-•]/m.test(text)) return undefined;
  return parsed.data;
}

export function applicableAlignment(alignment: unknown, slot: any, text: string) {
  const a=alignmentIntent(alignment,slot,text);
  if (!a) return undefined;
  const vertical=a.vertical==='center' && slot.h>=slot.size*2.2 ? 'center' : 'preserve';
  return a.horizontal==='preserve' && vertical==='preserve' ? undefined : {...a,vertical};
}
