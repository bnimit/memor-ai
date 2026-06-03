#!/usr/bin/env node
/**
 * postinstall — sets up the Python venv and installs memorable's Python deps.
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
  console.log("memorable-ai: setting up Python environment...");

  const python = findPython();
  if (!python) {
    console.error(
      "memorable-ai: Python 3.11+ is required but not found.\n" +
      "Install it from https://python.org or via your package manager."
    );
    process.exit(1);
  }

  const isWindows = process.platform === "win32";
  const venvPython = join(VENV, isWindows ? "Scripts" : "bin", isWindows ? "python.exe" : "python3");
  const pip = join(VENV, isWindows ? "Scripts" : "bin", "pip");

  // Check if already set up (venv exists + memorable importable)
  if (existsSync(venvPython)) {
    try {
      execFileSync(venvPython, ["-c", "import memorable"], { cwd: ROOT, stdio: "pipe" });
      console.log("memorable-ai: already set up, skipping. Run `memorable setup` to force re-install.");
      // Still ensure ~/.memorable exists
      const home = process.env.HOME || process.env.USERPROFILE;
      const memorableDir = join(home, ".memorable");
      if (!existsSync(memorableDir)) mkdirSync(memorableDir, { recursive: true });
      return;
    } catch {}
  }

  // Create venv if it doesn't exist
  if (!existsSync(venvPython)) {
    console.log(`memorable-ai: creating venv with ${python}...`);
    execFileSync(python, ["-m", "venv", VENV], { cwd: ROOT, stdio: "inherit" });
  }

  // Install Python deps
  console.log("memorable-ai: installing Python dependencies...");
  execFileSync(pip, ["install", "-e", ".[dev]"], { cwd: ROOT, stdio: "inherit" });

  // Install sentence-transformers (local embeddings)
  console.log("memorable-ai: installing sentence-transformers for local embeddings...");
  execFileSync(pip, ["install", "sentence-transformers>=3.0"], { cwd: ROOT, stdio: "inherit" });

  // Create ~/.memorable directory
  const home = process.env.HOME || process.env.USERPROFILE;
  const memorableDir = join(home, ".memorable");
  if (!existsSync(memorableDir)) {
    mkdirSync(memorableDir, { recursive: true });
    console.log(`memorable-ai: created ${memorableDir}`);
  }

  console.log("memorable-ai: setup complete! Run `memorable --help` to get started.");
}

main();
