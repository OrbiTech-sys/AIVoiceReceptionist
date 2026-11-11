// `npm run dev`: starts the backend and the frontend together, and stops both if either one stops.
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Other ports let a second copy run beside this one: DEV_API_PORT=8001 DEV_WEB_PORT=3001 npm run dev
const API_PORT = Number(process.env.DEV_API_PORT) || 8000;
const WEB_PORT = Number(process.env.DEV_WEB_PORT) || 3000;

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const python = path.join(root, "backend", ".venv", ...(isWindows ? ["Scripts", "python.exe"] : ["bin", "python"]));
const next = path.join(root, "node_modules", "next", "dist", "bin", "next");

function exitWith(message) {
  console.error(message);
  process.exit(1);
}

function isPortInUse(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

if (!existsSync(python)) exitWith("The backend isn't set up yet. Follow the Quick start in README.md first.");
if (!existsSync(next)) exitWith("The frontend packages aren't installed yet. Run: npm install");
for (const port of [API_PORT, WEB_PORT]) {
  if (await isPortInUse(port)) exitWith(`Port ${port} is already in use. Close whatever is using it, such as an earlier server, then try again.`);
}

const children = new Map();
let stopping = false;

function stopAll(exitCode) {
  if (stopping) return;
  stopping = true;
  for (const child of children.values()) {
    if (child.exitCode !== null) continue;
    // Next.js runs helper processes, and on Windows only taskkill stops those as well.
    if (isWindows) spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
    else child.kill();
  }
  process.exit(exitCode);
}

function start(name, command, args, env = {}) {
  const child = spawn(command, args, { cwd: root, stdio: "inherit", env: { ...process.env, ...env } });
  children.set(name, child);
  child.once("error", (error) => {
    console.error(`Could not start the ${name}: ${error.message}`);
    stopAll(1);
  });
  child.once("exit", (code) => {
    if (!stopping) console.error(`\nThe ${name} stopped, so everything is stopping.`);
    stopAll(code ?? 1);
  });
}

process.on("SIGINT", () => stopAll(0));
process.on("SIGTERM", () => stopAll(0));

start("backend", python, ["-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", String(API_PORT)]);

// The frontend forwards /api requests to the backend. Starting both at once let the browser's first requests arrive
// while the backend was still starting, which showed as "Failed to proxy … socket hang up". So wait until it answers.
async function backendReady(timeoutMs) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until && !stopping) {
    try {
      const res = await fetch(`http://127.0.0.1:${API_PORT}/api/health`, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

if (await backendReady(90_000)) console.log("The backend is ready, starting the frontend.\n");
else if (!stopping) console.error("The backend hasn't answered after 90 seconds; starting the frontend anyway.\n");
if (!stopping) start("frontend", process.execPath, [next, "dev", "-p", String(WEB_PORT)], { BACKEND_URL: `http://127.0.0.1:${API_PORT}` });
