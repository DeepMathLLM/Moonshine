<!--
{
  "name": "manage_skill",
  "handler": "manage_skill",
  "description": "Create, patch, edit, delete, write files for, or delete files from installed Agent Skills-compatible markdown skills.",
  "parameters": {
    "type": "object",
    "properties": {
      "operation": {
        "type": "string",
        "description": "One of: create, patch, edit, delete, write_file, delete_file."
      },
      "slug": {
        "type": "string",
        "description": "Installed skill slug."
      },
      "title": {
        "type": "string",
        "description": "Skill title for create or edit."
      },
      "description": {
        "type": "string",
        "description": "Skill description for create or edit."
      },
      "body": {
        "type": "string",
        "description": "Full markdown body for create or edit."
      },
      "summary": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Summary section content for portable skill rendering."
      },
      "execution_steps": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Execution Steps section content for portable skill rendering."
      },
      "tool_calls": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Tool Calls section content for portable skill rendering."
      },
      "file_references": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "File References section content for portable skill rendering."
      },
      "compatibility": {
        "type": "string",
        "description": "Optional Agent Skills compatibility note."
      },
      "allowed_tools": {
        "type": ["array", "string"],
        "items": {"type": "string"},
        "description": "Allowed tools expressed as a list or a space-separated string."
      },
      "purpose": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy alias for summary content."
      },
      "workflow": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy alias for execution_steps content."
      },
      "checklist": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy alias for execution_steps content."
      },
      "when_to_use": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy compatibility field; folded into summary content."
      },
      "inputs": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy compatibility field; folded into file references."
      },
      "output_contract": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Output Contract section content for portable skill rendering."
      },
      "examples": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Legacy compatibility field; folded into output contract."
      },
      "notes": {
        "type": ["string", "array"],
        "items": {"type": "string"},
        "description": "Notes section content for portable skill rendering."
      },
      "category": {
        "type": "string",
        "description": "Skill category label."
      },
      "tags": {
        "type": ["array", "string"],
        "items": {"type": "string"},
        "description": "Tag list or comma-separated tag string."
      },
      "overwrite": {
        "type": "boolean",
        "description": "Allow overwrite when creating a skill."
      },
      "old_text": {
        "type": "string",
        "description": "Exact text to replace for patch."
      },
      "new_text": {
        "type": "string",
        "description": "Replacement text for patch."
      },
      "replace_all": {
        "type": "boolean",
        "description": "Replace all matching occurrences when patching."
      },
      "relative_path": {
        "type": "string",
        "description": "Auxiliary file path relative to the skill directory."
      },
      "content": {
        "type": "string",
        "description": "Content for write_file."
      }
    },
    "required": ["operation", "slug"]
  }
}
-->

# Tool: manage_skill

## Usage Hint
- Use this tool to manage installed Agent Skills-compatible markdown skills.
- Use it when a user explicitly asks to create, update, inspect, or delete a skill.

