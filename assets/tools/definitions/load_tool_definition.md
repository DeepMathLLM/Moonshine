<!--
{
  "name": "load_tool_definition",
  "handler": "load_tool_definition",
  "description": "Load the full markdown definition for a tool after reviewing its summary in context.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "The tool name to inspect."}
    },
    "required": ["name"]
  }
}
-->

# Tool: load_tool_definition

## Usage Hint
- Use this tool to read a tool's full markdown guidance.
- Use it when the schema and compact prompt index do not provide enough guidance for correct tool use.

