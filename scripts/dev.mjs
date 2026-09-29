import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const children = ["backend", "frontend"].map((name) =>
  spawn("npm", ["run", "dev"], { cwd: root + name, stdio: "inherit" }),
);
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill("SIGTERM");
  setTimeout(() => process.exit(code), 300).unref();
}
children.forEach((child) => {
  child.on("error", () => stop(1));
  child.on("exit", (code) => stop(code ?? 1));
});
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
