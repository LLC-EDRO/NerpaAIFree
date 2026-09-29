import { AsyncLocalStorage } from 'node:async_hooks';
import type { Project } from './domain.js';

/** Host integration only: algorithms and prompts live in the copied engine. */
export interface EngineRuntime {
  workspace: string;
  locale?: 'ru' | 'en';
  instructions?: string;
  maxGeneratedImages?: number;
  env?: Record<string, string | undefined>;
  assertActive?: () => Promise<void>;
  onSave?: (project: Project) => Promise<void>;
  checkpointSnapshot?: (project: Project) => Project;
  onSlideReady?: (index: number, slide: any) => void;
  onRendered?: (slides: any[], output: string) => Promise<void>;
  writeDiagnosticDocument?: (path:string,value:unknown)=>void;
  flushDiagnostics?: ()=>Promise<void>;
  writeDocument?: (path: string, value: unknown) => Promise<void>;
  readDocument?: (path: string) => Promise<unknown | undefined>;
  saveNativeDocuments?: (directory: string, files: string[]) => Promise<void>;
  decorateSlides?: (slides: any[]) => Promise<void>;
  includeNotes?: boolean;
  describeSlide?: (index:number) => Promise<any>;
  descriptionsReady?: Promise<void>;
  prepareVisuals?: () => Promise<void>;
}
const contexts = new AsyncLocalStorage<EngineRuntime>();
export const engineContext = () => contexts.getStore();
export const withEngineContext = <T>(runtime: EngineRuntime, work: () => Promise<T>) => contexts.run(runtime, work);
// Per-invocation configuration prevents changing models of unrelated products.
export const engineEnv = new Proxy(process.env, {
  get(target, key: string) {
    const overrides = contexts.getStore()?.env;
    return overrides && Object.hasOwn(overrides, key) ? overrides[key] : target[key];
  },
});
