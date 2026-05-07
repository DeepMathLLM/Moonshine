<!--
{
  "name": "load_agent_definition",
  "handler": "load_agent_definition",
  "description": "Load the full markdown definition for an agent profile when its summary is not enough.",
  "parameters": {
    "type": "object",
    "properties": {
      "slug": {"type": "string", "description": "Optional agent slug. Defaults to the active agent profile."}
    }
  }
}
-->

# Tool: load_agent_definition

## Usage Hint
- Use this tool to read the full markdown definition of an agent profile.
- Use it when the active agent summary is insufficient for deciding how that agent should behave.

