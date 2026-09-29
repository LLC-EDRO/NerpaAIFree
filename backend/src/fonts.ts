import { engineEnv } from "./runtime-context.js";
import { resolve, join } from "node:path";
export const presentationFontRoot = () =>
  resolve(engineEnv.PRESENTATION_FONT_DIR || join(process.cwd(), "fonts"));

const familyKey = (name: string) => name.toLowerCase().replace(/[\s_-]+/g, "");
function familyClass(name: string) {
  if (/mono|courier|consolas|menlo/i.test(name)) return "mono";
  if (/times|georgia|cambria|garamond|serif/i.test(name) && !/sans/i.test(name)) return "serif";
  return "sans";
}
/** Only choose from the native engine's verified, installed font profile. */
export function similarAvailableFont(requested: string, available: string[], preferred?: string) {
  if (!available.length) throw new Error("No verified fonts available");
  const exact = available.find(font => familyKey(font) === familyKey(requested));
  if (exact) return exact;
  const sameClass = available.filter(font => familyClass(font) === familyClass(requested));
  const candidates = sameClass.length ? sameClass : available;
  return candidates.find(font => font === preferred) || candidates[0];
}
