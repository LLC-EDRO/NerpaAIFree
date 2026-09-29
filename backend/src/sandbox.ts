import { engineEnv, engineContext } from "./runtime-context.js";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { constants, rmSync } from "node:fs";
import {
  mkdir,
  mkdtemp,
  open,
  realpath,
  rm,
  stat,
  copyFile,
  rename,
  lstat,
  cp,
  readdir,
} from "node:fs/promises";
import { tmpdir, homedir } from "node:os";
import { basename, dirname, join } from "node:path";
import { randomUUID, createHash } from "node:crypto";
import {createSharedSnapshot} from './shared-snapshot.js';
import {createRendererSession,startWarmRenderer,type WarmRenderer} from './warm-renderer.js';
import { HttpError } from "./errors.js";
import { presentationFontRoot } from "./fonts.js";
import {
  receiveSandboxOutput,
  validateSandboxArtifacts,
} from "./sandbox-protocol.js";

const exec = promisify(execFile);
export function sandboxInputFiles(action: string, effectiveParser: boolean) {
  if(action==='font_cache')return [];
  if(action==='editor_patch')return ['source.pptx','previews/presentation.pdf'];
  if(action==='editor_background')return ['previews/presentation.pdf'];
  if (["font_requirements", "font_environment", "analyze"].includes(action))
    return ["source.pptx"];
  return [
    "source.pptx",
    "prepared.pptx",
    "analysis.json",
    "frames.json",
    "analyzer.json",
    effectiveParser ? "effective-parser.json" : "parser.json",
    ...(["export", "preview", "thumbnails", "assemble", "repair_context"].includes(action)
      ? ["previews/presentation.pdf"]
      : []),
  ];
}
const actions = new Set([
  'editor_background','editor_patch',
  'font_cache',
  "font_requirements",
  "font_environment",
  "analyze",
  "layout_metadata",
  "rebuild_context",
  "repair_context",
  "preflight",
  "fit",
  "export",
  "preview",
  "thumbnails",
  "assemble",
]);
const unavailable = () =>
  new HttpError(
    503,
    "Изолированный обработчик PPTX недоступен. Повторите позже; исходный файл и черновик сохранены.",
    "pptx_sandbox_unavailable",
  );
const binary = () => engineEnv.PRESENTATION_DOCKER_BIN || "docker";
const rendererActions=new Set(['thumbnails','preview','repair_context','export','editor_patch']);
const renderers=new WeakMap<object,ReturnType<typeof createRendererSession>>();
const rendererDisabled=new WeakSet<object>();
function rendererSession(folder?:string){
  const runtime=engineContext();
  if(!runtime||rendererDisabled.has(runtime)||engineEnv.PRESENTATION_WARM_RENDERER==='off'||engineEnv.PRESENTATION_FONT_CACHE==='off')return;
  let session=renderers.get(runtime);
  if(!session){
    session=createRendererSession(async()=>{
      const image=await nativeSandboxRevision(),fonts=await fontMounts('');
      if(process.getuid?.()===0)throw unavailable();
      return startWarmRenderer(binary(),(name,input)=>sandboxArguments({name,input,image,fonts,
        uid:process.getuid?.()||65532,gid:process.getgid?.()||65532}),async input=>{
          const source=folder||join(runtime.workspace,'source');
          let path=join(source,'prepared.pptx');
          try{await lstat(path);}catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT')throw error;path=join(source,'source.pptx');}
          await copyInput(path,join(input,'font-reference.pptx'),160*1024*1024);
        });
    });
    renderers.set(runtime,session);
  }
  return session;
}
export async function warmNativeRenderer(folder?:string){
  const runtime=engineContext();
  try{await rendererSession(folder)?.warm();}
  catch{if(runtime)rendererDisabled.add(runtime);console.warn(JSON.stringify({event:'presentation_warm_renderer_unavailable'}));}
}
export async function releaseNativeRenderer(runtime:object){
  const session=renderers.get(runtime);renderers.delete(runtime);rendererDisabled.delete(runtime);
  await session?.close();
}
const imageName = () =>
  engineEnv.PRESENTATION_SANDBOX_IMAGE || "nerpa-test-pptx:1";
let imageCache: { name: string; id: string; expires: number } | undefined;

/** Resolve a prebuilt image, never pull/build/fall back during a paid request. */
export async function nativeSandboxRevision() {
  const name = imageName();
  if (imageCache?.name === name && imageCache.expires > Date.now())
    return imageCache.id;
  try {
    const { stdout } = await exec(
      binary(),
      ["image", "inspect", "--format", "{{.Id}}", name],
      { timeout: 5000, maxBuffer: 4096 },
    );
    const id = stdout.trim();
    if (!/^sha256:[a-f0-9]{64}$/.test(id)) throw unavailable();
    imageCache = { name, id, expires: Date.now() + 30000 };
    return id;
  } catch {
    throw unavailable();
  }
}

export function sandboxArguments(input: {
  name: string;
  image: string;
  input: string;
  uid: number;
  gid: number;
  fonts: Array<{ source: string; target: string }>;
}) {
  const mount = (source: string, target: string) => {
    if (/[\r\n,]/.test(source) || !source.startsWith("/")) throw unavailable();
    return ["--mount", `type=bind,src=${source},dst=${target},readonly`];
  };
  return [
    "run",
    "--rm",
    "--pull=never",
    "--interactive",
    "--init",
    "--name",
    input.name,
    "--network=none",
    "--ipc=private",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true",
    "--memory=2g",
    "--memory-swap=2g",
    "--cpus=2",
    "--pids-limit=128",
    "--ulimit",
    "nofile=256:256",
    "--log-driver=none",
    "--user",
    `${input.uid}:${input.gid}`,
    "--tmpfs",
    `/work:rw,nosuid,nodev,noexec,size=768m,nr_inodes=16384,mode=700,uid=${input.uid},gid=${input.gid}`,
    "--tmpfs",
    `/tmp:rw,nosuid,nodev,noexec,size=64m,nr_inodes=4096,mode=700,uid=${input.uid},gid=${input.gid}`,
    ...mount(input.input, "/input"),
    ...input.fonts.flatMap((f) => mount(f.source, f.target)),
    input.image,
  ];
}

async function copyInput(source: string, target: string, max: number) {
  const file = await open(source, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > max)
      throw new HttpError(
        413,
        "PPTX превышает лимит обработки.",
        "pptx_input_limit",
      );
    await mkdir(dirname(target), { recursive: true, mode: 0o700 });
    const to = await open(target, "wx", 0o600);
    let count = 0;
    try {
      const buffer = Buffer.alloc(256 * 1024);
      for (;;) {
        const { bytesRead } = await file.read(buffer, 0, buffer.length, null);
        if (!bytesRead) break;
        count += bytesRead;
        if (count > max)
          throw new HttpError(
            413,
            "PPTX превышает лимит обработки.",
            "pptx_input_limit",
          );
        await to.writeFile(buffer.subarray(0, bytesRead));
      }
    } finally {
      await to.close();
    }
    return count;
  } finally {
    await file.close();
  }
}

export function nativeFontMounts() {
  // Only font directories; no HOME, backend tree, storage root or credentials.
  const root = presentationFontRoot();
  const entries = [
    { source: join(root, "open"), target: "/fonts/open" },
    { source: join(root, "licensed"), target: "/fonts/licensed" },
  ];
  if (
    process.platform === "darwin" &&
    engineEnv.PRESENTATION_FONT_SCOPE !== "library"
  )
    entries.push(
      { source: "/System/Library/Fonts", target: "/System/Library/Fonts" },
      { source: "/Library/Fonts", target: "/Library/Fonts" },
      {
        source: join(homedir(), "Library/Fonts"),
        target: "/usr/local/share/fonts/host-user",
      },
    );
  return entries;
}
type FontSnapshot={work:string;mounts:Array<{source:string;target:string}>};
const ownedFontSnapshots=new Set<string>();
process.once('exit',()=>{for(const path of ownedFontSnapshots)try{rmSync(path,{recursive:true,force:true});}catch{}});
const sharedFonts=createSharedSnapshot<FontSnapshot>(async s=>{await rm(s.work,{recursive:true,force:true});ownedFontSnapshots.delete(s.work);});
const fontSnapshots=new WeakMap<object,Promise<{value:FontSnapshot;release:()=>void}>>();
export async function releaseFontSnapshot(runtime:object){
  const snapshot=fontSnapshots.get(runtime);fontSnapshots.delete(runtime);
  if(snapshot)await snapshot.then(s=>s.release()).catch(()=>{});
}
/** Fingerprint installed font files, not presentation contents. Symlinks are
 * excluded in both fingerprint and copy, so an upload cannot populate it. */
export async function fontSnapshotKey(mounts:Array<{source:string;target:string}>,image:string){
  const hash=createHash('sha256').update(image);
  const walk=async(path:string):Promise<void>=>{
    const info=await lstat(path);
    if(info.isDirectory()){
      hash.update(path+'\0');
      for(const name of (await readdir(path)).sort())await walk(join(path,name));
    }else if(info.isFile()&&/\.(?:ttf|otf|ttc)$/i.test(path))hash.update(JSON.stringify([path,info.size,info.mtimeMs,info.ctimeMs]));
  };
  for(const mount of mounts){hash.update(mount.target);try{await walk(mount.source);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}}
  return hash.digest('hex');
}
export async function warmNativeFonts(){if(engineEnv.PRESENTATION_FONT_CACHE!=='off'&&engineContext())await fontMounts('');}
async function fontMounts(work:string){
  const runtime=engineContext();
  if(!runtime || engineEnv.PRESENTATION_FONT_CACHE==='off')return copyFontMounts(work);
  let snapshot=fontSnapshots.get(runtime);
  if(!snapshot){
    snapshot=(async()=>{
      const key=await fontSnapshotKey(nativeFontMounts(),await nativeSandboxRevision());
      return sharedFonts(key,async()=>{
        const folder=await mkdtemp(join(tmpdir(),'nerpa-font-snapshot-'));
        ownedFontSnapshots.add(folder);
        try{
          const mounts=await copyFontMounts(folder,true),cache=join(folder,'cache');
          // No PPTX, credentials or user workspace is mounted by this request.
          try{
            const result=await runNativeSandbox({action:'font_cache',folder,output:cache,fontDirectories:mounts.map(m=>m.target)},120000,()=>{},mounts) as {error?:string};
            if(result.error)throw new Error(result.error);
            return {work:folder,mounts:[...mounts,{source:cache,target:'/font-cache'}]};
          }catch{
            // A cache is an optimization, never a reason to reject a document.
            console.warn(JSON.stringify({event:'presentation_font_cache_unavailable'}));
            return {work:folder,mounts};
          }
        }catch(error){await rm(folder,{recursive:true,force:true});ownedFontSnapshots.delete(folder);throw error;}
      });
    })();
    fontSnapshots.set(runtime,snapshot);
  }
  return (await snapshot).value.mounts;
}
async function copyFontMounts(work: string, frozen=false) {
  const result = [];
  for (const entry of nativeFontMounts())
    try {
      if ((await stat(entry.source)).isDirectory()) {
        let source = await realpath(entry.source);
        if (process.platform === "darwin" || frozen) {
          // Desktop/TCC paths and /System are not safely bindable in Docker Desktop.
          // Font-only copy-on-write snapshots stay in this invocation's private temp
          // directory. No global sharing permissions or permanent copies are needed.
          source = join(work, "fonts", String(result.length));
          await cp(entry.source, source, {
            recursive: true,
            mode: constants.COPYFILE_FICLONE,
            preserveTimestamps:true,
            filter: async (path) => {
              const info = await lstat(path);
              return (
                info.isDirectory() ||
                (info.isFile() && /\.(?:ttf|otf|ttc)$/i.test(path))
              );
            },
          });
        }
        result.push({ ...entry, source });
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  return result;
}

/** Parsing uses disposable containers; rendering can reuse one isolated job
 * worker. Every call receives fresh private inputs and validates bounded output. */
export async function runNativeSandbox(
  request: Record<string, unknown>,
  timeoutMs: number,
  deferPersistence?: (directory:string, files:string[])=>void,
  preparedFonts?:Array<{source:string;target:string}>,
  options:{worker?:WarmRenderer;noWarm?:boolean;signal?:AbortSignal}={},
):Promise<any> {
  if (
    typeof request.action !== "string" ||
    !actions.has(request.action) ||
    typeof request.folder !== "string"
  )
    throw new Error("pptx_sandbox_request");
  options.signal?.throwIfAborted();
  if(!options.worker&&!options.noWarm&&rendererActions.has(request.action)){
    const session=rendererSession(request.folder);
    if(session){
      // A failed warmup may use the original converter. A failed conversion
      // still follows the existing bounded native retry/repair path.
      await warmNativeRenderer(request.folder);
      if(!rendererDisabled.has(engineContext()!))return session.run(worker=>
        runNativeSandbox(request,timeoutMs,deferPersistence,preparedFonts,{...options,worker}));
    }
  }
  const image = await nativeSandboxRevision();
  const inputKey=options.worker?randomUUID():undefined;
  const folder = await realpath(request.folder),
    work = options.worker?join(options.worker.input,inputKey!):await mkdtemp(join(tmpdir(), "nerpa-pptx-sandbox-"));
  const input = join(work, "input"),
    output = join(work, "result"),
    name = options.worker?.name||"nerpa-pptx-" + randomUUID();
  let child: ReturnType<typeof spawn> | undefined,
    timer: NodeJS.Timeout | undefined,
    expired = false,aborted=false;
  const removeContainer = async () => {
    await exec(binary(), ["rm", "--force", name], {
      timeout: 10000,
      maxBuffer: 4096,
    }).catch(() => {});
  };
  const abort=()=>{aborted=true;void removeContainer();child?.kill('SIGKILL');};
  try {
    if(options.worker)await mkdir(work,{mode:0o700});
    await mkdir(input);
    await mkdir(output);
    let inputBytes = 0;
    const effectiveParser = await lstat(
      join(folder, "effective-parser.json"),
    ).then(
      () => true,
      (error) => {
        if (error.code === "ENOENT") return false;
        throw error;
      },
    );
    for (const file of sandboxInputFiles(request.action, effectiveParser))
      try {
        inputBytes += await copyInput(
          join(folder, file),
          join(input, "template", file),
          160 * 1024 * 1024 - inputBytes,
        );
      } catch (error) {
        if (
          (error as NodeJS.ErrnoException).code !== "ENOENT" ||
          file === "source.pptx"
        )
          throw error;
      }
    const images = request.images ?? {};
    if (!images || typeof images !== "object" || Array.isArray(images))
      throw new Error("pptx_sandbox_request");
    const mapped: Record<string, string> = {};
    for (const [key, value] of Object.entries(images)) {
      if (
        !/^[a-f0-9]{64}\.png$/.test(key) ||
        typeof value !== "string" ||
        basename(value) !== key
      )
        throw new Error("pptx_sandbox_request");
      inputBytes += await copyInput(
        value,
        join(input, "images", key),
        Math.min(24 * 1024 * 1024, 256 * 1024 * 1024 - inputBytes),
      );
      mapped[key] = "/input/images/" + key;
    }
    const payload = JSON.stringify({
      ...request,
      folder: "/work/template",
      output: "/work/output",
      images: mapped,
      inputKey,
    });
    if (Buffer.byteLength(payload) > 8 * 1024 * 1024)
      throw new HttpError(
        413,
        "PPTX превышает лимит обработки.",
        "pptx_input_limit",
      );
    const uid = process.getuid?.() || 65532,
      gid = process.getgid?.() || 65532;
    // A non-root backend owns private inputs. Root deployment would make them
    // unreadable to the renderer; reject configuration rather than run as root.
    if (process.getuid?.() === 0) throw unavailable();
    options.signal?.throwIfAborted();
    child = spawn(
      binary(),
      options.worker?['exec','--interactive',name,'python','-I','/opt/pptx/sandbox_entry.py']:sandboxArguments({
        name,
        image,
        input,
        uid,
        gid,
        fonts: preparedFonts ?? await fontMounts(work),
      }),
      { stdio: ["pipe", "pipe", "pipe"] },
    );
    options.signal?.addEventListener('abort',abort,{once:true});
    if(options.signal?.aborted)abort();
    const proc = child;
    const closed = new Promise<number | null>((resolve, reject) => {
      proc.once("error", () => reject(unavailable()));
      proc.once("close", (code) => resolve(code));
    });
    // Attach immediately, including when stream validation fails before exit.
    void closed.catch(() => {});
    timer = setTimeout(() => {
      expired = true;
      void removeContainer();
      proc.kill("SIGKILL");
    }, timeoutMs);
    proc.stderr!.resume();
    proc.stdin!.on("error", () => {});
    proc.stdin!.end(payload + "\n");
    let received;
    try {
      received = await receiveSandboxOutput(
        proc.stdout!,
        request.action,
        output,
      );
    } catch (error) {
      if(aborted)options.signal?.throwIfAborted();
      if (expired)
        throw new HttpError(
          503,
          "Обработка PPTX превысила время ожидания.",
          "pptx_timeout",
        );
      await removeContainer();
      proc.kill("SIGKILL");
      const code = await closed.catch(() => null);
      if (code === 125 || code === 126 || code === 127) throw unavailable();
      if (code === 137)
        throw new HttpError(
          422,
          "PPTX превысил доступные ресурсы обработки. Исходный файл сохранён.",
          "pptx_resource_limit",
        );
      throw new HttpError(
        503,
        "Изолированный обработчик PPTX не завершил ответ.",
        "pptx_runtime_failed",
      );
    }
    const code = await closed;
    if(aborted)options.signal?.throwIfAborted();
    if (expired)
      throw new HttpError(
        503,
        "Обработка PPTX превысила время ожидания.",
        "pptx_timeout",
      );
    if (code !== 0)
      throw new HttpError(
        503,
        "Изолированный обработчик PPTX не завершил ответ.",
        "pptx_runtime_failed",
      );
    try {
      validateSandboxArtifacts(
        request.action,
        received.result,
        received.files,
        Array.isArray(request.slides) ? request.slides.length : 0,
      );
    } catch {
      throw new HttpError(
        503,
        "Изолированный обработчик PPTX вернул неполный результат.",
        "pptx_runtime_failed",
      );
    }
    const destination = ["editor_background", "editor_patch", "font_cache", "export", "preview", "thumbnails", "assemble", "repair_context"].includes(
      request.action,
    )
      ? request.output
      : folder;
    if (received.files.length) {
      if (typeof destination !== "string")
        throw new Error("pptx_sandbox_request");
      await mkdir(destination, { recursive: true, mode: 0o700 });
      for (const file of received.files) {
        const target = join(destination, file);
        await mkdir(dirname(target), { recursive: true, mode: 0o700 });
        if (
          (await realpath(dirname(target))) !==
          join(
            await realpath(destination),
            dirname(file) === "." ? "" : dirname(file),
          )
        )
          throw new Error("pptx_sandbox_output_path");
        try {
          if ((await lstat(target)).isSymbolicLink())
            throw new Error("pptx_sandbox_output_path");
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
        }
        const pending = target + "." + randomUUID() + ".tmp";
        try {
          await copyFile(join(output, file), pending, constants.COPYFILE_EXCL);
          await rename(pending, target);
        } finally {
          await rm(pending, { force: true });
        }
      }
    }
    if (typeof destination === 'string') {
      const documents=received.files.filter(file => file.endsWith('.json'));
      if(deferPersistence)deferPersistence(destination,documents);
      else await engineContext()?.saveNativeDocuments?.(destination,documents);
    }
    return received.result;
  } finally {
    options.signal?.removeEventListener('abort',abort);
    if (timer) clearTimeout(timer);
    if (child) {
      if(!options.worker)await removeContainer();
      child.kill("SIGKILL");
    }
    // Only this fresh invocation directory, never template/job storage.
    await rm(work, { recursive: true, force: true });
  }
}
