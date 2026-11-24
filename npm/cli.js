#!/usr/bin/env node
// npx duo-agents  — installs Duo (or updates it) and starts it. Needs only Node, Python 3.13+ and tmux.
// Duo never installs agent CLIs; it detects the ones you already have (claude, codex, agy).
"use strict";
const { spawnSync, spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const HOME = process.env.DUO_HOME || path.join(os.homedir(), "Duo");
const mac = process.platform === "darwin";
const say = (m) => console.log(m);
const out = (cmd, args) => {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  return r.status === 0 ? (r.stdout || "").trim() : null;
};

function fail(msg) { console.error("\nDuo install stopped: " + msg); process.exit(1); }

if (process.platform === "win32") {
  fail("Duo needs tmux, so on Windows it runs inside WSL (Ubuntu).\n" +
       "  1. In PowerShell:  wsl --install -d Ubuntu\n" +
       "  2. Open the Ubuntu app, then run:  npx duo-agents");
}

function findPython() {
  const names = [process.env.DUO_PYTHON, "python3.13", "python3.14", "python3", "python"].filter(Boolean);
  const extra = mac ? ["/opt/homebrew/bin/python3.13", "/usr/local/bin/python3.13"] : [];
  for (const c of [...names, ...extra]) {
    const v = out(c, ["-c", "import sys; print(sys.version_info >= (3, 13))"]);
    if (v === "True") return c;
  }
  return null;
}

function check() {
  const py = findPython();
  if (!py) {
    fail("Python 3.13 or newer was not found. One extra step:\n  " +
         (mac ? "brew install python@3.13" : "sudo apt install python3.13     (or: curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13)") +
         "\nThen run this command again.");
  }
  if (out("tmux", ["-V"]) === null) {
    fail("tmux was not found. Run:  " + (mac ? "brew install tmux" : "sudo apt install tmux") + "\nThen run this command again.");
  }
  return py;
}

function installed() {
  try {
    const s = JSON.parse(fs.readFileSync(path.join(HOME, "core", "state.json"), "utf8"));
    return s.current || null;
  } catch { return null; }
}

function report(py) {
  const found = [["claude", "Claude Code"], ["codex", "Codex"], ["agy", "Antigravity"]]
    .filter(([c]) => out("sh", ["-c", "command -v " + c]));
  say("Agent CLIs found: " + (found.length ? found.map((f) => f[1]).join(", ") : "none"));
  if (!found.length) say("Duo uses the CLIs you already have. Install and sign in to Claude Code, Codex or Antigravity, then run duo again.");
}

function install(py) {
  say("Installing Duo " + fs.readFileSync(path.join(ROOT, "CORE_VERSION"), "utf8").trim() + " into " + HOME);
  const r = spawnSync("sh", [path.join(ROOT, "install.sh")], {
    stdio: "inherit", env: { ...process.env, DUO_HOME: HOME, DUO_PYTHON: py },
  });
  if (r.status !== 0) process.exit(r.status || 1);
}

function main() {
  const args = process.argv.slice(2);
  if (args[0] === "--help" || args[0] === "-h") {
    say("npx duo-agents [task ...]\n  no arguments   install or update Duo, then start it and open the browser\n" +
        "  setup          install or update only\n  doctor         show what Duo can find\n  <anything else> is passed to the duo command");
    return;
  }
  const py = check();
  const had = installed();
  if (!had || args[0] === "setup" || args[0] === "update" || path.resolve(ROOT) !== path.resolve(process.cwd())) install(py);
  const bin = path.join(HOME, "bin", "duo");
  if (!fs.existsSync(bin)) fail("the duo command was not created in " + HOME + "/bin.");
  report(py);
  if (args[0] === "setup" || args[0] === "update") {
    say("\nDone. Open a new terminal and run:  duo");
    return;
  }
  const env = { ...process.env, DUO_HOME: HOME };
  const child = spawn(bin, args, { stdio: "inherit", env });
  child.on("exit", (code) => process.exit(code || 0));
}

main();
