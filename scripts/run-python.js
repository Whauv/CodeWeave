const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function resolvePythonCommand() {
  if (process.env.CODEWEAVE_PYTHON) {
    return process.env.CODEWEAVE_PYTHON;
  }
  const venvPython = process.platform === "win32"
    ? path.join(".venv", "Scripts", "python.exe")
    : path.join(".venv", "bin", "python");
  return fs.existsSync(venvPython) ? venvPython : "python";
}

const pythonCommand = resolvePythonCommand();
const args = process.argv.slice(2);
const result = spawnSync(pythonCommand, args, { stdio: "inherit" });

if (result.error) {
  console.error(result.error.message || result.error);
  process.exit(1);
}
process.exit(typeof result.status === "number" ? result.status : 1);
