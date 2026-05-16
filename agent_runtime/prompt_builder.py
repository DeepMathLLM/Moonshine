"""Prompt assembly for Moonshine."""

from __future__ import annotations

from typing import Dict, List, Optional

from moonshine.agent_runtime.memory_provider import ContextBundle
from moonshine.agent_runtime.research_mode import build_research_mode_policy


def build_system_prompt(
    mode: str,
    project_slug: str,
    context: ContextBundle,
    tool_names: List[str],
    tool_index: Optional[str] = None,
    skill_index: Optional[str] = None,
    agent_summary: Optional[str] = None,
    agent_body: Optional[str] = None,
    mcp_index: Optional[str] = None,
    research_runtime_context: Optional[str] = None,
) -> str:
    """Build the system prompt for a conversation turn."""
    lines = [
        "You are Moonshine: an independent mathematical and technical researcher with explicit evidence, project context, and auxiliary tool support.",
        "Carry the current project or conversation forward directly rather than narrating it from the outside.",
        "Use retrieval when prior context, decisions, or previous work may change the answer.",
        "Think and reason in the assistant turn itself; use tools and files to support the work rather than to replace the work.",
        "Tool schemas are attached to each main model call.",
        "When a task matches a listed skill's usage guidance, load that skill with `load_skill_definition` before relying on its workflow, unless the step is trivial or the full definition is already in context.",
        "Use relevant tools and MCP tools when they materially help retrieval, file inspection, verification, experiments, or external context; do not rely on free-text claims when an available tool can provide evidence.",
        "Skills provide detailed working methods; tools provide executable actions.",
    ]
    if mode != "research":
        lines.append(
            "Answer directly and concisely, and retrieve prior context only when it materially helps the current turn."
        )
    if mode == "research":
        lines.append("")
        lines.append(build_research_mode_policy(project_slug))
        lines.append("")
        lines.append(
            "Research skill/tool contract: actively match the current research step to the available skill and tool indexes. "
            "For problem generation, refinement, proof construction, counterexample search, verification, correction, consolidation, novelty recording, retrieval, or synthesis, load the corresponding skill definition before doing substantial work. "
            "Use the corresponding tools for reading, retrieval, verification, experiments, and final review."
        )
        runtime_text = str(research_runtime_context or "").strip()
        if runtime_text:
            lines.append("")
            lines.append(runtime_text)
    for index_text in (tool_index, skill_index, mcp_index):
        rendered_index = str(index_text or "").strip()
        if rendered_index:
            lines.append("")
            lines.append(rendered_index)
    active_agent_text = str(agent_body or "").strip()
    if active_agent_text:
        lines.append("")
        lines.append("Active agent instructions:")
        lines.append(active_agent_text)
    context_text = context.to_prompt_text()
    if context_text:
        lines.append("")
        lines.append(context_text)
    return "\n".join(lines).strip()


def build_provider_messages(recent_messages: List[Dict[str, object]], user_message: str) -> List[Dict[str, str]]:
    """Prepare provider messages from recent history plus the new user input."""
    messages = [{"role": item["role"], "content": str(item["content"])} for item in recent_messages]
    messages.append({"role": "user", "content": user_message})
    return messages
