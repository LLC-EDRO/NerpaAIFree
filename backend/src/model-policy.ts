export type AvailableModel = {
  id: string;
  name: string;
  sizeB: number;
  license: string;
  text: boolean;
  vision: boolean;
};

/** Only the requested local multimodal model is selectable. */
export function eligibleModel(raw: any): AvailableModel | undefined {
  const id = raw?.id;
  if (typeof id !== "string" || !/^gemma4:12b(?:[-_][\w.-]+)?$/i.test(id)) return;
  return { id, name: id, sizeB: 12, license: "apache-2.0", text: true, vision: true };
}
