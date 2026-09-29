import { mkdir, open, type FileHandle } from "node:fs/promises";
import { dirname, join } from "node:path";

export const sandboxOutputLimit = 256 * 1024 * 1024;
const lineLimit = 8 * 1024 * 1024;
export function sandboxArtifactAllowed(action: string, name: string) {
  if (action === 'editor_patch') return /^(?:presentation\.(?:pptx|pdf)|slide-\d{1,3}\.png)$/.test(name);
  if (action === 'editor_background') return name === 'slide-0.png' || /^[a-f0-9]{64}\.ttf$/.test(name);
  if (action === 'font_cache') return name === 'font-index.json' || /^fontconfig\/[a-f0-9]{32}-[a-z0-9]+\.cache-\d+$/.test(name);
  if (action === 'thumbnails') return /^slide-\d{1,3}\.png$/.test(name);
  if (action === 'repair_context') return /^slide-\d{1,3}\.png$/.test(name);
  if (action === "assemble") return /^(?:presentation\.pptx|source-preview\.pdf|(?:analysis|frames|effective-parser|analyzer)\.json)$/.test(name);
  if (action === "preview") return name === "slide-0.png";
  if (action === "export")
    return /^(?:presentation\.(?:pptx|pdf)|slide-\d{1,3}\.png|(?:render-quality|package-cleanup|field-changes)\.json)$/.test(
      name,
    );
  if (action === "analyze")
    return /^(?:prepared\.pptx|(?:analysis|frames|parser|effective-parser|analyzer)\.json|previews\/(?:presentation\.pdf|slide-\d{1,3}\.png))$/.test(
      name,
    );
  return false;
}

/** A successful reply cannot mark a partial render as complete. */
export function validateSandboxArtifacts(
  action: string,
  result: any,
  files: string[],
  slides: number,
) {
  if (result.error) return;
  const required: string[] = [];
  if(action==='editor_patch'){
    if(!result.pptxWritten||!result.pdfAvailable||!Array.isArray(result.changedSlides)||!result.changedSlides.length)throw new Error('pptx_sandbox_incomplete');
    required.push('presentation.pptx','presentation.pdf',...result.changedSlides.map((i:number)=>`slide-${i}.png`));
  }
  if(action==='editor_background') {
    if(!Array.isArray(result.paths)||!Array.isArray(result.fonts)||result.fonts.length>64)throw new Error('pptx_sandbox_incomplete');
    required.push('slide-0.png',...result.fonts.map((f:any)=>f.key));
  }
  if (action === 'font_cache') {
    if (!Number.isInteger(result.fonts) || result.fonts<1) throw new Error('pptx_sandbox_incomplete');
    if (!files.some(name=>name.startsWith('fontconfig/'))) throw new Error('pptx_sandbox_incomplete');
    required.push('font-index.json');
  }
  if (action === 'thumbnails') {
    if (!Number.isInteger(slides) || slides<1 || slides>100 || result.slides!==slides)
      throw new Error('pptx_sandbox_incomplete');
    required.push(...Array.from({length:slides},(_,i)=>`slide-${i}.png`));
  }
  if (action === 'repair_context') {
    if (!Number.isInteger(result.blankSlides) || result.blankSlides<1 || result.blankSlides>100 || !result.profiles || Object.keys(result.profiles).length!==result.blankSlides)
      throw new Error('pptx_sandbox_incomplete');
    required.push(...Array.from({length:result.blankSlides},(_,i)=>`slide-${i}.png`));
  }
  if (action === "preflight" && result.ready !== true)
    throw new Error("pptx_sandbox_incomplete");
  if (action === "assemble") required.push("presentation.pptx", "analysis.json", "frames.json", "effective-parser.json", "analyzer.json");
  if (action === "analyze") {
    if (
      !Array.isArray(result.layouts) ||
      result.layouts.length < 1 ||
      result.layouts.length > 100
    )
      throw new Error("pptx_sandbox_incomplete");
    required.push(
      "analysis.json",
      "frames.json",
      "parser.json",
      "effective-parser.json",
      "analyzer.json",
      "previews/presentation.pdf",
      ...result.layouts.map(
        (_: unknown, i: number) => `previews/slide-${i}.png`,
      ),
    );
    if (result.preparedSha256) required.push("prepared.pptx");
  } else if (action === "preview") {
    if (slides !== 1 || !Array.isArray(result.issues))
      throw new Error("pptx_sandbox_incomplete");
    if (!result.issues.length) required.push("slide-0.png");
  } else if (action === "export") {
    if (!Array.isArray(result.issues))
      throw new Error("pptx_sandbox_incomplete");
    if (result.pptxWritten || !result.issues.length) {
      required.push("presentation.pptx", "package-cleanup.json", "render-quality.json");
      if (result.pdfAvailable !== false)
        required.push("presentation.pdf", ...Array.from({length:slides},(_,i)=>`slide-${i}.png`));
      if (Array.isArray(result.fieldChanges)) required.push("field-changes.json");
    }
  }
  if (required.some((name) => !files.includes(name)))
    throw new Error("pptx_sandbox_incomplete");
}

/** Untrusted framed stdout, NOT a tar extractor. Reject paths, links, duplicate
 * names, oversized streams and truncation before anything is published. */
export async function receiveSandboxOutput(
  stream: AsyncIterable<Buffer>,
  action: string,
  folder: string,
) {
  let buffer = Buffer.alloc(0),
    result: unknown,
    seenResult = false,
    done = false,
    total = 0;
  let file: FileHandle | undefined,
    remaining = 0;
  const files: string[] = [];
  const invalid = () => new Error("pptx_sandbox_protocol");
  try {
    for await (const chunk of stream) {
      buffer = Buffer.concat([buffer, chunk]);
      for (let at; (at = buffer.indexOf(10)) >= 0;) {
        if (at > lineLimit || done) throw invalid();
        const value = JSON.parse(buffer.subarray(0, at).toString("utf8"));
        buffer = buffer.subarray(at + 1);
        if (!value || typeof value !== "object" || Array.isArray(value))
          throw invalid();
        if (!seenResult) {
          if (
            !value.result ||
            typeof value.result !== "object" ||
            Array.isArray(value.result)
          )
            throw invalid();
          result = value.result;
          seenResult = true;
          continue;
        }
        if (value.file !== undefined) {
          if (
            file ||
            typeof value.file !== "string" ||
            !sandboxArtifactAllowed(action, value.file) ||
            files.includes(value.file) ||
            files.length >= (action === 'font_cache' ? 4096 : 220) ||
            !Number.isSafeInteger(value.size) ||
            value.size < 0 ||
            value.size > 160 * 1024 * 1024
          )
            throw invalid();
          total += value.size;
          if (total > sandboxOutputLimit) throw invalid();
          const path = join(folder, value.file);
          await mkdir(dirname(path), { recursive: true, mode: 0o700 });
          file = await open(path, "wx", 0o600);
          remaining = value.size;
          files.push(value.file);
        } else if (value.chunk !== undefined) {
          if (
            !file ||
            typeof value.chunk !== "string" ||
            !value.chunk.length ||
            value.chunk.length > 65536 ||
            value.chunk.length % 4 ||
            !/^[A-Za-z0-9+/]*={0,2}$/.test(value.chunk)
          )
            throw invalid();
          const bytes = Buffer.from(value.chunk, "base64");
          remaining -= bytes.length;
          if (remaining < 0) throw invalid();
          await file.writeFile(bytes);
        } else if (value.endFile === true) {
          if (!file || remaining !== 0) throw invalid();
          await file.close();
          file = undefined;
        } else if (value.done === true) {
          if (file) throw invalid();
          done = true;
        } else throw invalid();
      }
      if (buffer.length > lineLimit) throw invalid();
    }
    if (!seenResult || !done || file || buffer.length) throw invalid();
    if ((result as { error?: unknown }).error && files.length) throw invalid();
    return { result, files, total };
  } finally {
    await file?.close();
  }
}
