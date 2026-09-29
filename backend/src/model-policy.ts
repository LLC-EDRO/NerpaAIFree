/** The public lab accepts documented open-source chat/VL models only.
 * Names are suggestions, not bundled weights or working demo inference. */
export const demoModels = [
  { id: "demo:qwen3:14b", name: "Пример · Qwen3 14B (текст)", sizeB: 14.8, text: true, vision: false },
  { id: "demo:qwen3-vl:8b", name: "Пример · Qwen3-VL 8B (изображения)", sizeB: 8.8, text: true, vision: true },
] as const;

export type AvailableModel = {
  id: string;
  name: string;
  sizeB: number;
  license: string;
  text: boolean;
  vision: boolean;
};

const openLicenses = new Set(["apache-2.0", "apache 2.0", "mit", "bsd-3-clause", "bsd-2-clause"]);
const prohibited = /(?:^|[/_:.\-])(llama|deepseek|openai|gpt)(?:$|[/_:.\-\d])/i;
const sizeFrom = (value: unknown): number | undefined => {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return value / 1e9;
  if (typeof value !== "string") return;
  const match = value.match(/(\d+(?:\.\d+)?)\s*(b|billion|m|million)\b/i);
  if (!match) return;
  return Number(match[1]) * (/^m/i.test(match[2]) ? 0.001 : 1);
};

function curated(id: string): { sizeB: number; vision: boolean } | undefined {
  const name = id.toLowerCase();
  // Explicit size tags only. "latest" and aliases can silently point to a larger model.
  const match = name.match(/^(?:qwen\/)?qwen3(-vl)?(?::|-)(0\.6|1\.7|2|4|8|14)b(?:$|[-_:])/);
  if (!match) return;
  const size = Number(match[2]);
  if (match[1]) return [2, 4, 8].includes(size) ? { sizeB: size, vision: true } : undefined;
  return [0.6, 1.7, 4, 8, 14].includes(size) ? { sizeB: size, vision: false } : undefined;
}

export function eligibleModel(raw: any): AvailableModel | undefined {
  if (typeof raw?.id !== "string" || raw.id.length > 300 || prohibited.test(raw.id)) return;
  const known = curated(raw.id);
  const license = String(raw?.meta?.license ?? raw?.license ?? raw?.details?.license ?? (known ? "apache-2.0" : "")).toLowerCase();
  if (!openLicenses.has(license)) return;
  const sizeB = sizeFrom(raw?.details?.parameter_size ?? raw?.meta?.parameter_size ?? raw?.parameter_size) ?? known?.sizeB;
  if (!sizeB || sizeB > 27) return;
  const capabilities = raw?.meta?.capabilities ?? raw?.capabilities;
  const modalities = raw?.meta?.modalities ?? raw?.modalities;
  const vision = known?.vision === true || capabilities?.vision === true || (Array.isArray(modalities) && modalities.includes("image"));
  const text = !/(?:^|[-_])(?:embed|rerank)(?:$|[-_])/i.test(raw.id);
  if (!text) return;
  return {
    id: raw.id,
    name: typeof raw.name === "string" ? raw.name.slice(0, 300) : raw.id,
    sizeB,
    license,
    text: sizeB <= 27,
    vision: vision && sizeB <= 20,
  };
}
