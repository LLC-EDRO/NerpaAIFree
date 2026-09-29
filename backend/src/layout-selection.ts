import type {Layout} from './domain.js';
import {regularTables, maxTableRows} from './table-structure.js';

/** Capabilities come from parsed OOXML objects, never a slide name or preview. */
export function layoutEditability(layout:Layout) {
  const nativeIds=[...new Set(layout.slots.filter(s=>s.cell).map(s=>s.shapeId))];
  const adaptable=regularTables(layout);
  const converted=new Set<number>(layout.drawnTableShapeIds || []);
  return {
    nativeTables:nativeIds.filter(id=>!converted.has(id)).length,
    recoveredEditableTables:nativeIds.filter(id=>converted.has(id)).length,
    nativeCharts:layout.charts.length,
    staticVectorCharts:(layout.vectorCharts || []).length,
    textFields:layout.slots.filter(s=>!s.cell).length,
    tables:adaptable.map(t=>({shapeId:t.shapeId,canResizeRows:true,canReduceColumns:true,maxTotalRows:maxTableRows,maxDataRows:t.maxDataRows,columns:t.columnCount})),
    graphicDataEditable:layout.charts.length>0 || nativeIds.length>0,
    // A graphic with no table/chart object must not be advertised as data-editable.
    unboundArtwork:layout.visuals?.unlabelledShapes || 0,
  };
}

export function preferEditableLayouts(layouts:Layout[]) {
  const rank=(l:Layout)=>{
    const c=layoutEditability(l);
    return (c.nativeCharts || c.nativeTables ? 0 : c.recoveredEditableTables ? 1 : c.textFields ? 2 : 3) + (l.requiresRebuild?4:0);
  };
  return [...layouts].sort((a,b)=>rank(a)-rank(b));
}

export const layoutSelectionPolicy = `Приоритеты выбора макетов:
Редактируемость данных обязательна для корректной статистики. Проверяй editability: nativeTables/nativeCharts — реальные объекты PowerPoint, их значения и структура редактируются. recoveredEditableTables уже преобразованы в настоящие таблицы, тоже допустимы. Для сравнения и статистики при равной смысловой пригодности сначала выбирай нативную таблицу/диаграмму, затем восстановленную таблицу, затем обычные редактируемые текстовые показатели. Не считай картинку или набор фигур редактируемой диаграммой: замена подписей не изменяет её значения, столбцы или сектора. При отсутствии подходящего нативного объекта используй честное текстовое сравнение; не заполняй нарисованные графики произвольными цифрами. Макеты с непонятной привязкой данных и большим unboundArtwork используй реже. Для таблиц число записей определяется материалами, не образцом; максимум 10 строк вместе с заголовком. При нехватке данных уменьши таблицу, не придумывай записи, свойства и накопительные итоги.
Содержание прежде иллюстраций. Предпочитай разнообразные макеты с цифрами, статистикой, редактируемыми графиками, таблицами, схемами и содержательным текстом, когда для них есть данные. Не выдумывай цифры ради числового макета. Сначала используй разные подходящие макеты и compositionFamily. Не повторяй один макет подряд; повтор допустим, если альтернативы хуже передают смысл. Титульные и разделители не заменяют содержательные слайды. При равной смысловой пригодности выбирай макет без requiresRepair.
maxGeneratedImages — верхняя граница, а не необходимое количество. Минимум — ноль. Не выбирай фото-макеты ради бесплатного лимита. В images перечисляй ТОЛЬКО нужные новые иллюстрации, суммарно не больше maxGeneratedImages, только на внутренних слайдах. Для остальных — images:[]. Это уточнение имеет приоритет над требованием описать каждый visualSlot. Исходные картинки, фон, логотипы и иконки сохраняются без генерации. Используй крупную иллюстрацию лишь когда она объясняет сюжет лучше данных; native charts и таблицы предпочтительнее растровых диаграмм. Не занимай большую часть слайдов картинками.`;
