<!--
{
  "name": "store_conclusion",
  "handler": "store_conclusion",
  "description": "Promote a structured conclusion into knowledge, or keep it as a project candidate when the research gate is not satisfied.",
  "parameters": {
    "type": "object",
    "properties": {
      "title": {"type": "string", "description": "Conclusion title."},
      "statement": {"type": "string", "description": "Structured statement of the conclusion."},
      "proof_sketch": {"type": "string", "description": "Optional proof sketch or evidence summary."},
      "project_slug": {"type": "string", "description": "Optional project scope."},
      "status": {"type": "string", "description": "Conclusion confidence status such as verified or partial."}
    },
    "required": ["title", "statement"]
  }
}
-->

# Tool: store_conclusion

## Usage Hint
- Use this tool to store a verified or stable mathematical conclusion.
- Use it when a claim is important enough to be reused in future project or knowledge retrieval.

