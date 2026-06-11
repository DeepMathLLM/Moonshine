<!--
{
  "name": "install_python_package",
  "handler": "install_python_package",
  "description": "Install Python packages into the current Moonshine Python environment when project scripts require missing libraries.",
  "parameters": {
    "type": "object",
    "properties": {
      "packages": {"type": "array", "items": {"type": "string"}, "description": "One or more pip package requirement strings, such as sympy, numpy==2.0.0, or scipy>=1.12. Raw pip options, paths, URLs, and VCS installs are rejected."},
      "timeout_seconds": {"type": "integer", "description": "Optional timeout in seconds. Values are clamped to a bounded installation window."},
      "upgrade": {"type": "boolean", "description": "Whether to pass --upgrade for the listed packages."}
    },
    "required": ["packages"],
    "additionalProperties": false
  }
}
-->

# Tool: install_python_package

## Usage Hint
- Use this tool when a Python script fails because an importable package is missing or a known package version is required for a bounded computation.
- Install only the specific packages needed for the current project script, then rerun the script with `run_python_script`.
- This installs with the same Python interpreter that runs Moonshine, which is usually the active virtual environment.
- Do not use this tool for shell commands, system packages, local paths, URLs, VCS repositories, requirements files, or broad environment maintenance.

## Behavior
- Runs `python -m pip install` with the current Moonshine Python interpreter.
- Uses the active project directory as the working directory.
- Captures `stdout`, `stderr`, return code, timeout status, package specs, and whether the interpreter appears to be in a virtual environment.
- Rejects raw pip options, filesystem paths, URLs, and VCS/local installs.
