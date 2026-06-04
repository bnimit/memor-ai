#!/usr/bin/env node
/**
 * memor CLI — thin wrapper that delegates to the Python CLI.
 * Handles venv discovery and `setup` re-run.
 */
import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const isWindows = process.platform === "win32";
const VENV_BIN = join(ROOT, ".venv", isWindows ? "Scripts" : "bin");
const PYTHON = join(VENV_BIN, isWindows ? "python.exe" : "python3");

function ensureSetup() {
  if (!existsSync(PYTHON)) {
    console.error(
      "memor-ai: Python venv not found. Running setup...\n" +
      "If this fails, run: node scripts/postinstall.mjs"
    );
    try {
      execFileSync("node", [join(ROOT, "scripts", "postinstall.mjs")], { stdio: "inherit" });
    } catch {
      process.exit(1);
    }
  }
}

function main() {
  const args = process.argv.slice(2);
  const command = args[0];

  // `memor setup` — re-run postinstall
  if (command === "setup") {
    execFileSync("node", [join(ROOT, "scripts", "postinstall.mjs")], { stdio: "inherit" });
    return;
  }

  ensureSetup();

  // Everything else (including `inspector`) → delegate to the Python CLI
  const child = spawn(PYTHON, ["-m", "memor.cli", ...args], {
    cwd: process.cwd(),
    stdio: "inherit",
    env: { ...process.env, PATH: `${VENV_BIN}:${process.env.PATH}` },
  });
  child.on("exit", (code) => process.exit(code || 0));
}

main();
