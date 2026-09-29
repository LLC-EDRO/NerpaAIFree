import type {Project,Layout} from './domain.js';

/** Metadata adds geometry constraints; slide identity/order belong to analysis. */
export function mergeSourceLayoutMetadata(layouts:Layout[], metadata:Record<string,Partial<Layout>>) {
  return layouts.map(layout=>({...layout,...metadata[layout.id],id:layout.id,index:layout.index,part:layout.part,usable:layout.usable,warnings:layout.warnings}));
}

/** Upload already enriches these fields against the same effective PPTX. Source
 * recovery replaces analysis, so an old/incomplete analysis falls back to the
 * native reader. Do not treat an absent visualSlots as a confirmed empty list. */
export function sourceLayoutMetadata(p:Project):Record<string,Layout>|undefined {
  const layouts:Layout[]|undefined=p.analysis?.layouts;
  if(!layouts?.length || layouts.some(l=>!Array.isArray(l.slots)||!Array.isArray(l.visualSlots)))return;
  return Object.fromEntries(layouts.map(l=>[l.id,l]));
}
