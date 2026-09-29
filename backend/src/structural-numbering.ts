/** Detect document ordinals without exempting numbers in the actual claim. */
export function structuralPrefixes(fields: {key: string; text: string; role?: string; cell?: unknown}[]) {
  const result = new Map<string,string>();
  const groups = new Map<string, {key:string; number:number; prefix:string}[]>();
  for (const field of fields) {
    if (field.cell || field.role === 'metric') continue;
    const text = field.text;
    const named = /^(?:шаг|этап|пункт|step|stage)\s+(\d{1,2})(?:\s*[.)\]:—–-])?\s+(?=[\p{L}«“"(])/iu.exec(text);
    const numbered = /^(\d{1,2})(?:\s*([.)\]:—–-])\s*|\s+)(?=[\p{L}«“"(])/u.exec(text);
    const glued = /^(0[1-9])(?=\p{L})/u.exec(text);
    const match = named || numbered || glued;
    if (!match) continue;
    const rest = text.slice(match[0].length);
    // Quantities and calendar dates may start a sentence but aren't list labels.
    if (/^(?:%|₽|руб[\p{L}.]*|тыс[\p{L}.]*|млн|млрд|миллион[\p{L}.]*|миллиард[\p{L}.]*|процент[\p{L}.]*|январ[\p{L}.]*|феврал[\p{L}.]*|март[\p{L}.]*|апрел[\p{L}.]*|ма[йя]|июн[\p{L}.]*|июл[\p{L}.]*|август[\p{L}.]*|сентябр[\p{L}.]*|октябр[\p{L}.]*|ноябр[\p{L}.]*|декабр[\p{L}.]*)(?:\s|$|[.,])/iu.test(rest)) continue;
    const explicit = !!named || !!numbered?.[2];
    const padded = /^0\d$/.test(match[1]);
    if (!explicit && !padded && field.role !== 'step_number') continue;
    // A named step is explicit structure even when each step has its own slide.
    if (named || field.role === 'step_number') result.set(field.key,match[0]);
    const format = named ? 'named' : explicit ? 'punctuated' : 'spaced';
    const items = groups.get(format) || [];
    items.push({key:field.key,number:Number(match[1]),prefix:match[0]});
    groups.set(format,items);
  }
  for (const items of groups.values()) {
    items.sort((a,b)=>a.number-b.number);
    if (items.length>=2 && items.every((item,i)=>item.number===items[0].number+i && item.number>=1))
      for (const item of items) result.set(item.key,item.prefix);
  }
  return result;
}

/** Keep a verified source ordinal when the model shortens the heading body. */
export function preserveStructuralPrefix(text:string, prefix:string) {
  const candidate = structuralPrefixes([{key:'value',text,role:'step_number'}]).get('value');
  const originalNumber = prefix.match(/\d+/)?.[0];
  const nextNumber = candidate?.match(/\d+/)?.[0];
  const body = candidate && Number(originalNumber) === Number(nextNumber) ? text.slice(candidate.length) : text;
  return prefix.trimEnd() + ' ' + body.trimStart();
}

/** A source cohort proves standalone step badges. Bare quantities and table
 * cells never become numbering merely because their values are consecutive. */
export function structuralOrdinalMarkers(fields: {key:string;text:string;role?:string;cell?:unknown}[]) {
  const groups = new Map<string, {key:string;value:number;text:string}[]>();
  for (const field of fields) {
    if (field.cell || ['metric','page_number','unit','brand'].includes(field.role || '')) continue;
    const match = /^\s*(\d{1,2})([.)])?\s*$/.exec(field.text);
    if (!match || (!match[2] && !/^0\d$/.test(match[1]) && field.role !== 'step_number')) continue;
    const format = `${match[1].startsWith('0') ? 'padded' : 'plain'}:${match[2] || ''}`;
    const group = groups.get(format) || [];
    group.push({key:field.key,value:Number(match[1]),text:field.text});
    groups.set(format,group);
  }
  const result = new Map<string,string>();
  for (const group of groups.values()) {
    group.sort((a,b)=>a.value-b.value);
    if (group.length >= 2 && group.every((item,i)=>item.value >= 1 && item.value === group[0].value+i))
      for (const item of group) result.set(item.key,item.text);
  }
  return result;
}
