<!--
{
  "name": "load_skill_definition",
  "handler": "load_skill_definition",
  "description": "Load the full markdown manual for a skill. This reads instructions only; it does not execute the skill.",
  "parameters": {
    "type": "object",
    "properties": {
      "slug": {"type": "string", "description": "The skill slug to load."}
    },
    "required": ["slug"]
  }
}
-->

# Tool: load_skill_definition

## Usage Hint
- Use this tool to read the full workflow for a skill whose summary matches the current task.
- Use it when a nontrivial step should follow a skill rather than relying only on the compact prompt index.

