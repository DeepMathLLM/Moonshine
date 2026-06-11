<!--
{
  "name": "run_python_script",
  "handler": "run_python_script",
  "description": "Run a bounded Python script inside the active project directory for computations, experiments, or reproducible checks.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Path to a .py script relative to the active project root. Legacy projects/<active-project>/... paths are accepted and normalized. Absolute paths are accepted only when they stay inside the active project."},
      "args": {"type": "array", "items": {"type": "string"}, "description": "Optional command-line arguments passed to the script as strings."},
      "timeout_seconds": {"type": "integer", "description": "Optional timeout in seconds. Values are clamped to a small bounded execution window."}
    },
    "required": ["path"],
    "additionalProperties": false
  }
}
-->

# Tool: run_python_script

## Usage Hint
- Use this tool when a project-local Python script can provide numerical evidence, symbolic checks, counterexample search, data inspection, or reproducible computation for the current task.
- First create or inspect the script with filesystem/read tools, then run the project-relative `.py` path with bounded arguments and timeout.
- Use this for bounded Python execution only; do not use it for shell commands, package installation, long-running services, or interactive programs. When a script fails because a package is missing, use `install_python_package` and rerun the script.
- Prefer scripts under project folders such as `workspace/`, `experiments/`, or `scratch/` so the computation is reproducible from the project directory.

## Behavior
- Runs the script with the current Python interpreter.
- Uses the active project directory as the working directory.
- Captures `stdout`, `stderr`, return code, timeout status, resolved path, and project root.
- Rejects paths outside the active project and rejects non-`.py` files.
