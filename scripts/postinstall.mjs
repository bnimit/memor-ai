#!/usr/bin/env node
/**
 * postinstall — sets up the Python venv and installs memor's Python deps.
 * Runs automatically on `npm install` / `bun install`.
 */
import { execSync, execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const VENV = join(ROOT, ".venv");

function findPython() {
  for (const cmd of ["python3", "python"]) {
    try {
      const version = execFileSync(cmd, ["--version"], { encoding: "utf8" }).trim();
      const match = version.match(/(\d+)\.(\d+)/);
      if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 11))) {
        return cmd;
      }
    } catch {}
  }
  return null;
}

function main() {
  console.log("memor-ai: setting up Python environment...");

  const python = findPython();
  if (!python) {
    console.error(
      "memor-ai: Python 3.11+ is required but not found.\n" +
      "Install it from https://python.org or via your package manager."
    );
    process.exit(1);
  }

  const isWindows = process.platform === "win32";
  const venvPython = join(VENV, isWindows ? "Scripts" : "bin", isWindows ? "python.exe" : "python3");
  const pip = join(VENV, isWindows ? "Scripts" : "bin", "pip");

  // Check if already set up (venv exists + memor importable)
  if (existsSync(venvPython)) {
    try {
      execFileSync(venvPython, ["-c", "import memor"], { cwd: ROOT, stdio: "pipe" });
      console.log("memor-ai: already set up, skipping. Run `memor setup` to force re-install.");
      const home = process.env.HOME || process.env.USERPROFILE;
      const memorDir = join(home, ".memor");
      if (!existsSync(memorDir)) mkdirSync(memorDir, { recursive: true });
      return;
    } catch {}
  }

  // Create venv if it doesn't exist
  if (!existsSync(venvPython)) {
    console.log(`memor-ai: creating venv with ${python}...`);
    execFileSync(python, ["-m", "venv", VENV], { cwd: ROOT, stdio: "inherit" });
  }

  // Install Python deps
  console.log("memor-ai: installing Python dependencies...");
  execFileSync(pip, ["install", "-e", ".[dev]"], { cwd: ROOT, stdio: "inherit" });

  // Install sentence-transformers (local embeddings)
  console.log("memor-ai: installing sentence-transformers for local embeddings...");
  execFileSync(pip, ["install", "sentence-transformers>=3.0"], { cwd: ROOT, stdio: "inherit" });

  // Create ~/.memor directory
  const home = process.env.HOME || process.env.USERPROFILE;
  const memorDir = join(home, ".memor");
  if (!existsSync(memorDir)) {
    mkdirSync(memorDir, { recursive: true });
    console.log(`memor-ai: created ${memorDir}`);
  }

  console.log("memor-ai: setup complete! Run `memor --help` to get started.");
}

main();
