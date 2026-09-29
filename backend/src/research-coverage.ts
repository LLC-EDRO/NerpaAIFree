import { z } from 'zod';
import type { Fact } from './llm-context.js';

/** An empty missing-list is accepted only after every slide is accounted for.
 * Numeric layouts must point to actual numeric evidence, never model memory. */
export function validateResearchCoverage(raw: unknown, count: number, numericSlides: Set<number>, facts: Fact[]) {
  const rows = z.object({slides: z.array(z.object({
    slide: z.number().int().min(1).max(count),
    evidenceIds: z.array(z.string()),
    missing: z.string().max(700),
  }))}).parse(raw).slides;
  if(rows.length !== count || new Set(rows.map(s=>s.slide)).size !== count) throw new Error('Incomplete research coverage');
  const byId = new Map(facts.map(f=>[f.id,f]));
  for(const row of rows) {
    if(row.evidenceIds.some(id=>!byId.has(id))) throw new Error('Unknown evidence ID');
    if(!row.missing && numericSlides.has(row.slide) && !row.evidenceIds.some(id=>/\d/.test(byId.get(id)!.text)))
      throw new Error('Numeric slide requires cited numeric evidence or a missing research need');
  }
  return rows;
}
