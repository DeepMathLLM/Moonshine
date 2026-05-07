"""Adaptive Research Mode workflow with optional LangGraph checkpointing."""

from __future__ import annotations

import json
import re
import shutil
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from moonshine.agent_runtime.research_index import ResearchIndexStore, stable_claim_hash
from moonshine.agent_runtime.research_log import (
    RESEARCH_ARCHIVE_SCHEMA,
    RESEARCH_LOG_TYPES,
    ResearchLogStore,
    render_research_log_for_archive,
)
from moonshine.json_schema import validate_json_schema
from moonshine.moonshine_constants import (
    RESEARCH_MEMORY_CHANNELS,
    normalize_research_channel_name,
)
from moonshine.providers import OfflineProvider
from moonshine.utils import (
    append_jsonl,
    atomic_write,
    deterministic_slug,
    estimate_token_count,
    overlap_score,
    read_json,
    read_jsonl,
    read_text,
    shorten,
    utc_now,
)


WORKFLOW_VERSION = 3

RESEARCH_STAGES = ["problem_design", "problem_solving"]

PROBLEM_DESIGN_ACTIVITIES = [
    "literature_scan",
    "cross_domain_explore",
    "problem_generation",
    "quality_evaluation",
    "problem_refinement",
    "design_checkpoint",
]

PROBLEM_SOLVING_ACTIVITIES = [
    "problem_decomposition",
    "solver_branching",
    "lemma_extraction",
    "proof_integration",
    "pessimistic_verification",
    "correction",
    "strengthening",
    "persistence",
]

RESEARCH_ACTIVITY_ORDER = PROBLEM_DESIGN_ACTIVITIES + PROBLEM_SOLVING_ACTIVITIES
RESEARCH_ACTIVITY_ENUM = RESEARCH_ACTIVITY_ORDER + ["stay"]

ACTIVITIES_BY_STAGE = {
    "problem_design": PROBLEM_DESIGN_ACTIVITIES,
    "problem_solving": PROBLEM_SOLVING_ACTIVITIES,
}

ACTIVITY_SPECS: Dict[str, Dict[str, object]] = {
    "literature_scan": {
        "stage": "problem_design",
        "label": "LiteratureScan",
        "goal": "Survey project-local references and relevant known results.",
        "skills": ["literature-survey", "query-memory"],
        "tools": ["query_memory", "search_knowledge", "read_runtime_file"],
        "good_outputs": ["source-labeled summary", "definitions", "known obstructions", "open gaps"],
    },
    "cross_domain_explore": {
        "stage": "problem_design",
        "label": "CrossDomainExplore",
        "goal": "Explore analogies and transferable techniques from adjacent mathematical domains.",
        "skills": ["cross-domain-explore", "construct-toy-examples"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["analogies", "transfer risks", "toy examples"],
    },
    "problem_generation": {
        "stage": "problem_design",
        "label": "ProblemGeneration",
        "goal": "Generate candidate research problems from accumulated context.",
        "skills": ["problem-generator"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["candidate statements", "motivation", "expected dependencies"],
    },
    "quality_evaluation": {
        "stage": "problem_design",
        "label": "QualityEvaluation",
        "goal": "Evaluate candidate problems by Impact 40%, Feasibility 25%, Novelty 20%, Richness 15%.",
        "skills": ["quality-assessor"],
        "tools": ["assess_problem_quality", "query_memory", "search_knowledge"],
        "good_outputs": ["weighted scores", "selected problem", "weaknesses", "refinement advice"],
    },
    "problem_refinement": {
        "stage": "problem_design",
        "label": "ProblemRefinement",
        "goal": "Refine, narrow, strengthen, or reformulate weak candidate problems.",
        "skills": ["problem-refiner", "literature-survey", "cross-domain-explore"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["revised statement", "changed assumptions", "reason for iteration"],
    },
    "design_checkpoint": {
        "stage": "problem_design",
        "label": "DesignCheckpoint",
        "goal": "Consolidate design-stage progress before continuing or entering solving.",
        "skills": ["research-consolidation"],
        "tools": ["query_memory"],
        "good_outputs": ["chosen problem", "discarded candidates", "next design action"],
    },
    "problem_decomposition": {
        "stage": "problem_solving",
        "label": "ProblemDecomposition",
        "goal": "Break the selected problem into subgoals, branches, and dependency constraints.",
        "skills": ["propose-subgoal-decomposition"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["subgoals", "dependency graph", "branch plan"],
    },
    "solver_branching": {
        "stage": "problem_solving",
        "label": "SolverBranching",
        "goal": "Work on one or more proof branches, including examples and counterexamples.",
        "skills": ["problem-solver", "lemma-prover", "direct-proving", "construct-toy-examples", "construct-counterexamples"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["solve steps", "failed paths", "examples", "lemma candidates"],
    },
    "lemma_extraction": {
        "stage": "problem_solving",
        "label": "LemmaExtraction",
        "goal": "Extract reusable intermediate claims into project memory and knowledge memory.",
        "skills": ["verify-overall", "conclusion-manage", "extract-conclusion-memory"],
        "tools": ["verify_overall", "query_memory"],
        "good_outputs": ["stored lemmas", "claim status", "evidence"],
    },
    "proof_integration": {
        "stage": "problem_solving",
        "label": "ProofIntegration",
        "goal": "Integrate accepted subclaims into a coherent formal blueprint.",
        "skills": ["proof-constructor"],
        "tools": ["query_memory", "search_knowledge", "verify_overall"],
        "good_outputs": ["formal blueprint", "dependency notes", "remaining gaps"],
    },
    "pessimistic_verification": {
        "stage": "problem_solving",
        "label": "PessimisticVerification",
        "goal": "Pessimistically audit assumption usage, calculations, and logic; only a full multidimensional pass establishes correctness.",
        "skills": [
            "verify-overall",
            "verify-correctness-assumption",
            "verify-correctness-computation",
            "verify-correctness-logic",
        ],
        "tools": [
            "verify_overall",
            "verify_correctness_assumption",
            "verify_correctness_computation",
            "verify_correctness_logic",
            "query_memory",
            "search_knowledge",
        ],
        "good_outputs": ["dimension verdicts", "aggregated errors", "repair targets"],
    },
    "correction": {
        "stage": "problem_solving",
        "label": "Correction",
        "goal": "Repair verifier failures or identify why the current branch should be abandoned.",
        "skills": ["proof-corrector", "identify-key-failures"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["correction plan", "patched argument", "failed path"],
    },
    "strengthening": {
        "stage": "problem_solving",
        "label": "Strengthening",
        "goal": "After repeated failures, adjust assumptions or formulate a stronger tractable result.",
        "skills": ["strengthening-agent", "problem-refiner"],
        "tools": ["query_memory", "search_knowledge"],
        "good_outputs": ["revised theorem", "new assumptions", "new branch"],
    },
    "persistence": {
        "stage": "problem_solving",
        "label": "Persistence",
        "goal": "Persist final or partial progress and make future continuation easy.",
        "skills": ["research-consolidation", "conclusion-manage"],
        "tools": ["query_memory", "read_runtime_file"],
        "good_outputs": ["progress checkpoint", "stored knowledge", "next actions"],
    },
}

# Backward-compatible alias for code/tests that still use "node" terminology.
NODE_SPECS = ACTIVITY_SPECS


QUALITY_SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "impact": {"type": "number"},
        "feasibility": {"type": "number"},
        "novelty": {"type": "number"},
        "richness": {"type": "number"},
        "overall": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["impact", "feasibility", "novelty", "richness", "overall", "rationale"],
}


STATE_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "current_focus": {"type": "string", "minLength": 1},
        "search_sufficiency": {
            "type": "string",
            "enum": ["insufficient", "adequate", "excessive", "not_relevant"],
        },
        "reasoning_state": {
            "type": "string",
            "enum": ["starting", "making_progress", "stuck", "repairing", "ready_to_verify", "verified_partial"],
        },
        "memory_need": {
            "type": "string",
            "enum": ["none", "query_project", "query_all_projects", "write_project", "write_knowledge"],
        },
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "rationale": {"type": "string"},
    },
    "required": [
        "current_focus",
        "search_sufficiency",
        "reasoning_state",
        "memory_need",
        "risk_level",
        "rationale",
    ],
}


CONTROL_SELECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selected_skills": {"type": "array", "items": {"type": "string"}},
        "selected_tools": {"type": "array", "items": {"type": "string"}},
        "trigger_rules_used": {"type": "array", "items": {"type": "string"}},
        "selection_rationale": {"type": "string"},
    },
    "required": ["selected_skills", "selected_tools", "trigger_rules_used", "selection_rationale"],
}


INTERMEDIATE_VERIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "needed": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["not_needed", "needed", "passed", "failed"]},
        "targets": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["needed", "verdict", "targets", "rationale"],
}


FINAL_VERIFICATION_GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "has_complete_answer": {"type": "boolean"},
        "ready_for_final_verification": {"type": "boolean"},
        "blueprint_path": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["has_complete_answer", "ready_for_final_verification", "blueprint_path", "reason"],
}


RESEARCH_ARTIFACTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "immediate_conclusions": {"type": "array", "items": {"type": "string"}},
        "toy_examples": {"type": "array", "items": {"type": "string"}},
        "counterexamples": {"type": "array", "items": {"type": "string"}},
        "big_decisions": {"type": "array", "items": {"type": "string"}},
        "special_case_checks": {"type": "array", "items": {"type": "string"}},
        "novelty_notes": {"type": "array", "items": {"type": "string"}},
        "subgoals": {"type": "array", "items": {"type": "string"}},
        "solve_steps": {"type": "array", "items": {"type": "string"}},
        "failed_paths": {"type": "array", "items": {"type": "string"}},
        "verification_reports": {"type": "array", "items": {"type": "string"}},
        "branch_states": {"type": "array", "items": {"type": "string"}},
        "events": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(RESEARCH_MEMORY_CHANNELS),
}


INSTRUCTION_CONFLICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "source": {
            "type": "string",
            "enum": ["project_agents", "builtin_research_control", "model_output", "unknown"],
        },
        "conflict": {"type": "string", "minLength": 1},
        "winning_rule": {"type": "string", "minLength": 1},
        "resolution": {"type": "string", "minLength": 1},
    },
    "required": ["source", "conflict", "winning_rule", "resolution"],
}


GLOBAL_HARD_INVARIANTS = [
    "Do not promote unverified claims into formal knowledge memory; keep them as candidate project lemmas.",
    "Only run final verification when a complete proof blueprint or answer exists.",
    "Any serious verifier objection means the proof is not established.",
    "If repeated repair attempts fail, change direction rather than continuing local patches.",
    "Persist important intermediate artifacts, especially failed paths, branch states, solve attempts, and verification reports.",
]

AUTO_PROBLEM_DRAFT_MARKER = "<!-- moonshine:auto-problem-draft -->"

SECTION_ALIASES = {
    "problem_draft": ["problem draft", "active problem", "current problem"],
    "candidate_problem": ["candidate problem", "candidate problems"],
    "problem_review": ["problem review", "quality review"],
    "stage_transition": ["stage transition", "stage decision", "design decision"],
    "toy_example": ["toy example", "example"],
    "counterexample": ["counterexample", "counterexamples"],
    "special_case_check": ["special case check", "special-case check", "special cases", "special-case examination"],
    "novelty_note": ["novelty note", "novelty record", "novelty assessment"],
    "subgoal_plan": ["subgoal plan", "decomposition plan", "subgoals"],
    "failed_path": ["failed path", "failed direction", "failure"],
    "branch_update": ["branch update", "branch state"],
    "solve_attempt": ["solve attempt", "solve attempts", "solve step", "solve steps"],
    "checkpoint": ["checkpoint", "next steps", "consolidation"],
}


SKILL_ACTIVITY_HINTS = {
    "literature-survey": "literature_scan",
    "query-memory": "literature_scan",
    "cross-domain-explore": "cross_domain_explore",
    "problem-generator": "problem_generation",
    "quality-assessor": "quality_evaluation",
    "problem-refiner": "problem_refinement",
    "research-consolidation": "design_checkpoint",
    "propose-subgoal-decomposition": "problem_decomposition",
    "problem-solver": "solver_branching",
    "lemma-prover": "solver_branching",
    "direct-proving": "solver_branching",
    "construct-toy-examples": "solver_branching",
    "construct-counterexamples": "solver_branching",
    "examination-of-special-cases-neural-network-functions": "quality_evaluation",
    "record-novelty": "persistence",
    "conclusion-manage": "lemma_extraction",
    "extract-conclusion-memory": "lemma_extraction",
    "proof-constructor": "proof_integration",
    "pessimistic-verifier": "pessimistic_verification",
    "verify-overall": "pessimistic_verification",
    "verify-correctness-assumption": "pessimistic_verification",
    "verify-correctness-computation": "pessimistic_verification",
    "verify-correctness-logic": "pessimistic_verification",
    "proof-corrector": "correction",
    "identify-key-failures": "correction",
    "strengthening-agent": "strengthening",
}


RESEARCH_CHANNEL_DESCRIPTIONS = {
    "immediate_conclusions": "stored immediate claims, lemma candidates, and concise intermediate conclusions",
    "toy_examples": "toy examples, sanity checks, and small constructions used for intuition",
    "counterexamples": "counterexamples and edge cases that test or refute fragile claims",
    "big_decisions": "problem reviews, active-problem choices, stage decisions, and major research-direction decisions",
    "special_case_checks": "tested special cases that stress-check whether a formulation survives concrete low-complexity instances",
    "novelty_notes": "durable notes about valuable new concepts, methods, or theory-level contributions extracted from the current branch, solution, or exploration",
    "subgoals": "decomposition plans, subgoal structures, and branch-level proof planning",
    "solve_steps": "general solving steps, solve attempts, construction progress, and local argument progress",
    "failed_paths": "dead branches, invalidated strategies, recurring blockers, and why they failed",
    "verification_reports": "multidimensional verification outcomes, dimension verdicts, critical errors, gaps, and repair targets",
    "branch_states": "branch updates, branch status snapshots, and strategy-state changes",
    "events": "checkpoints, retrieval notes, and procedural research events",
}


# Legacy schema retained for backwards-compatible parsing helpers and tests.
# The live research loop now uses research_log archival for project memory.
RESEARCH_WORKFLOW_UPDATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "state_assessment": STATE_ASSESSMENT_SCHEMA,
        "control_selection": CONTROL_SELECTION_SCHEMA,
        "activity_status": {
            "type": "string",
            "enum": ["in_progress", "checkpointed", "blocked", "completed"],
        },
        "recommended_next_activity": {"type": "string", "enum": RESEARCH_ACTIVITY_ENUM},
        "stage_decision": {
            "type": "string",
            "enum": ["stay_in_stage", "advance_to_problem_solving", "return_to_problem_design", "complete_project"],
        },
        "active_problem": {"type": "string"},
        "candidate_problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "statement": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["title", "statement", "rationale"],
            },
        },
        "quality_scores": QUALITY_SCORE_SCHEMA,
        "verification": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["not_checked", "verified", "needs_correction", "incorrect"],
                },
                "critical_errors": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
            },
            "required": ["verdict", "critical_errors", "rationale"],
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "failed_paths": {"type": "array", "items": {"type": "string"}},
        "research_artifacts": RESEARCH_ARTIFACTS_SCHEMA,
        "instruction_conflicts": {
            "type": "array",
            "items": INSTRUCTION_CONFLICT_SCHEMA,
        },
        "branch_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "branch_id": {"type": "string"},
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["branch_id", "status", "summary"],
            },
        },
        "conclusions_to_store": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "statement": {"type": "string", "minLength": 1},
                    "proof_sketch": {"type": "string"},
                    "status": {"type": "string", "enum": ["draft", "partial", "verified", "refuted"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "statement", "proof_sketch", "status", "tags"],
            },
        },
        "intermediate_verification": INTERMEDIATE_VERIFICATION_SCHEMA,
        "final_verification_gate": FINAL_VERIFICATION_GATE_SCHEMA,
        "memory_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "alias": {
                        "type": "string",
                        "enum": ["project-context", "project-decisions", "project-lemmas", "project-progress"],
                    },
                    "title": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "body": {"type": "string", "minLength": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["alias", "title", "summary", "body", "tags"],
            },
        },
        "summary": {"type": "string", "minLength": 1},
        "next_action": {"type": "string"},
        "controller_rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "state_assessment",
        "control_selection",
        "activity_status",
        "recommended_next_activity",
        "stage_decision",
        "active_problem",
        "candidate_problems",
        "quality_scores",
        "verification",
        "open_questions",
        "failed_paths",
        "research_artifacts",
        "instruction_conflicts",
        "branch_updates",
        "conclusions_to_store",
        "intermediate_verification",
        "final_verification_gate",
        "memory_updates",
        "summary",
        "next_action",
        "controller_rationale",
        "confidence",
    ],
}


@dataclass
class ResearchWorkflowState:
    """Durable adaptive workflow state for one research project."""

    project_slug: str
    stage: str = "problem_design"
    node: str = "literature_scan"
    status: str = "active"
    active_branch_id: str = ""
    current_focus: str = ""
    current_claim: str = ""
    blocker: str = ""
    active_problem: str = ""
    candidate_problems: List[Dict[str, str]] = field(default_factory=list)
    problem_review: Dict[str, object] = field(default_factory=dict)
    quality_scores: Dict[str, object] = field(default_factory=dict)
    verification: Dict[str, object] = field(default_factory=dict)
    completed_nodes: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    failed_paths: List[str] = field(default_factory=list)
    branch_states: List[Dict[str, str]] = field(default_factory=list)
    claim_registry: List[Dict[str, object]] = field(default_factory=list)
    verified_claim_hashes: List[str] = field(default_factory=list)
    workspace_hashes: Dict[str, str] = field(default_factory=dict)
    recent_artifacts: List[Dict[str, object]] = field(default_factory=list)
    transition_status: Dict[str, object] = field(default_factory=dict)
    state_assessment: Dict[str, object] = field(default_factory=dict)
    selected_skills: List[str] = field(default_factory=list)
    selected_tools: List[str] = field(default_factory=list)
    pending_verification_items: List[str] = field(default_factory=list)
    final_verification_gate: Dict[str, object] = field(default_factory=dict)
    iteration_count: int = 0
    design_iteration_count: int = 0
    solving_iteration_count: int = 0
    correction_attempts: int = 0
    strengthening_attempts: int = 0
    last_summary: str = ""
    next_action: str = ""
    recent_memory_ids: List[str] = field(default_factory=list)
    checkpoint_backend: str = "file_jsonl"
    langgraph_thread_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    version: int = WORKFLOW_VERSION

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "project_slug": self.project_slug,
            "stage": self.stage,
            "node": self.node,
            "focus_activity": self.node,
            "status": self.status,
            "active_branch_id": self.active_branch_id,
            "current_focus": self.current_focus,
            "current_claim": self.current_claim,
            "blocker": self.blocker,
            "active_problem": self.active_problem,
            "candidate_problems": list(self.candidate_problems),
            "problem_review": dict(self.problem_review),
            "quality_scores": dict(self.quality_scores),
            "verification": dict(self.verification),
            "completed_nodes": list(self.completed_nodes),
            "completed_activities": list(self.completed_nodes),
            "open_questions": list(self.open_questions),
            "failed_paths": list(self.failed_paths),
            "branch_states": list(self.branch_states),
            "claim_registry": list(self.claim_registry),
            "verified_claim_hashes": list(self.verified_claim_hashes),
            "workspace_hashes": dict(self.workspace_hashes),
            "recent_artifacts": list(self.recent_artifacts),
            "transition_status": dict(self.transition_status),
            "state_assessment": dict(self.state_assessment),
            "selected_skills": list(self.selected_skills),
            "selected_tools": list(self.selected_tools),
            "pending_verification_items": list(self.pending_verification_items),
            "final_verification_gate": dict(self.final_verification_gate),
            "iteration_count": self.iteration_count,
            "design_iteration_count": self.design_iteration_count,
            "solving_iteration_count": self.solving_iteration_count,
            "correction_attempts": self.correction_attempts,
            "strengthening_attempts": self.strengthening_attempts,
            "last_summary": self.last_summary,
            "next_action": self.next_action,
            "recent_memory_ids": list(self.recent_memory_ids),
            "checkpoint_backend": self.checkpoint_backend,
            "langgraph_thread_id": self.langgraph_thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _default_quality_scores() -> Dict[str, object]:
    return {
        "impact": 0.0,
        "feasibility": 0.0,
        "novelty": 0.0,
        "richness": 0.0,
        "overall": 0.0,
        "rationale": "",
    }


def _default_verification() -> Dict[str, object]:
    return {"verdict": "not_checked", "critical_errors": [], "rationale": ""}


def _default_problem_review() -> Dict[str, object]:
    return {
        "title": "",
        "summary": "",
        "review_status": "not_reviewed",
        "passed": False,
        "quality_scores": _default_quality_scores(),
        "updated_at": "",
    }


def _default_state_assessment() -> Dict[str, object]:
    return {
        "current_focus": "Assess the current research state.",
        "search_sufficiency": "insufficient",
        "reasoning_state": "starting",
        "memory_need": "query_project",
        "risk_level": "medium",
        "rationale": "",
    }


def _default_control_selection() -> Dict[str, object]:
    return {
        "selected_skills": [],
        "selected_tools": [],
        "trigger_rules_used": [],
        "selection_rationale": "",
    }


def _default_intermediate_verification() -> Dict[str, object]:
    return {"needed": False, "verdict": "not_needed", "targets": [], "rationale": ""}


def _default_final_verification_gate() -> Dict[str, object]:
    return {
        "has_complete_answer": False,
        "ready_for_final_verification": False,
        "blueprint_path": "",
        "reason": "",
    }


def _default_transition_status() -> Dict[str, object]:
    return {
        "last_attempted_stage": "",
        "approved": False,
        "reason": "",
        "updated_at": "",
    }


def _empty_research_artifacts() -> Dict[str, List[str]]:
    return {channel: [] for channel in RESEARCH_MEMORY_CHANNELS}


def _safe_stage(value: str) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in ACTIVITIES_BY_STAGE else "problem_design"


def _safe_activity(value: str, stage: Optional[str] = None) -> str:
    normalized = str(value or "").strip()
    if normalized in ACTIVITY_SPECS:
        if stage and ACTIVITY_SPECS[normalized]["stage"] != stage:
            return _default_activity_for_stage(stage)
        return normalized
    return _default_activity_for_stage(stage or "problem_design")


def _default_activity_for_stage(stage: str) -> str:
    safe_stage = _safe_stage(stage)
    return ACTIVITIES_BY_STAGE[safe_stage][0]


def _stage_for_activity(activity: str) -> str:
    return str(ACTIVITY_SPECS[_safe_activity(activity)]["stage"])


def _dedupe_strings(values: List[object], limit: int = 20) -> List[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _relative_to_home(paths, path: Path) -> str:
    try:
        return path.relative_to(paths.home).as_posix()
    except ValueError:
        return path.as_posix()


def _stable_text_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _state_from_payload(payload: Dict[str, object], project_slug: str) -> ResearchWorkflowState:
    created_at = str(payload.get("created_at") or utc_now())
    stage = _safe_stage(str(payload.get("stage") or "problem_design"))
    activity = str(payload.get("focus_activity") or payload.get("node") or _default_activity_for_stage(stage))
    if activity in ACTIVITY_SPECS:
        stage = _safe_stage(str(ACTIVITY_SPECS[activity]["stage"]))
    activity = _safe_activity(activity, stage)
    return ResearchWorkflowState(
        project_slug=str(payload.get("project_slug") or project_slug),
        stage=stage,
        node=activity,
        status=str(payload.get("status") or "active"),
        active_branch_id=str(payload.get("active_branch_id") or ""),
        current_focus=str(payload.get("current_focus") or ""),
        current_claim=str(payload.get("current_claim") or ""),
        blocker=str(payload.get("blocker") or ""),
        active_problem=str(payload.get("active_problem") or ""),
        candidate_problems=[dict(item) for item in list(payload.get("candidate_problems") or []) if isinstance(item, dict)],
        problem_review=dict(payload.get("problem_review") or _default_problem_review()),
        quality_scores=dict(payload.get("quality_scores") or _default_quality_scores()),
        verification=dict(payload.get("verification") or _default_verification()),
        completed_nodes=[str(item) for item in list(payload.get("completed_activities") or payload.get("completed_nodes") or [])],
        open_questions=_dedupe_strings(list(payload.get("open_questions") or [])),
        failed_paths=_dedupe_strings(list(payload.get("failed_paths") or [])),
        branch_states=[dict(item) for item in list(payload.get("branch_states") or []) if isinstance(item, dict)],
        claim_registry=[dict(item) for item in list(payload.get("claim_registry") or []) if isinstance(item, dict)][-64:],
        verified_claim_hashes=_dedupe_strings(list(payload.get("verified_claim_hashes") or []), limit=64),
        workspace_hashes=dict(payload.get("workspace_hashes") or {}),
        recent_artifacts=[dict(item) for item in list(payload.get("recent_artifacts") or []) if isinstance(item, dict)][-12:],
        transition_status=dict(payload.get("transition_status") or _default_transition_status()),
        state_assessment=dict(payload.get("state_assessment") or _default_state_assessment()),
        selected_skills=[str(item) for item in list(payload.get("selected_skills") or [])],
        selected_tools=[str(item) for item in list(payload.get("selected_tools") or [])],
        pending_verification_items=_dedupe_strings(list(payload.get("pending_verification_items") or [])),
        final_verification_gate=dict(payload.get("final_verification_gate") or _default_final_verification_gate()),
        iteration_count=int(payload.get("iteration_count", 0) or 0),
        design_iteration_count=int(payload.get("design_iteration_count", 0) or 0),
        solving_iteration_count=int(payload.get("solving_iteration_count", 0) or 0),
        correction_attempts=int(payload.get("correction_attempts", 0) or 0),
        strengthening_attempts=int(payload.get("strengthening_attempts", 0) or 0),
        last_summary=str(payload.get("last_summary") or ""),
        next_action=str(payload.get("next_action") or ""),
        recent_memory_ids=_dedupe_strings(list(payload.get("recent_memory_ids") or []), limit=16),
        checkpoint_backend=str(payload.get("checkpoint_backend") or "file_jsonl"),
        langgraph_thread_id=str(payload.get("langgraph_thread_id") or "moonshine-research-%s" % project_slug),
        created_at=created_at,
        updated_at=str(payload.get("updated_at") or created_at),
        version=int(payload.get("version", WORKFLOW_VERSION) or WORKFLOW_VERSION),
    )


class LangGraphCheckpointAdapter(object):
    """Tiny optional LangGraph checkpoint bridge plus durable JSONL snapshots."""

    def __init__(self, paths):
        self.paths = paths
        self.available = False
        self.error = ""
        self._graph = None
        try:
            from langgraph.checkpoint.memory import MemorySaver
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(dict)
            graph.add_node("checkpoint", lambda state: dict(state))
            graph.add_edge(START, "checkpoint")
            graph.add_edge("checkpoint", END)
            self._graph = graph.compile(checkpointer=MemorySaver())
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on optional package.
            self.error = str(exc)
            self._graph = None

    def save(self, *, project_slug: str, snapshot: Dict[str, object]) -> Dict[str, object]:
        """Save a visualizable checkpoint and return backend metadata."""
        checkpoint = {
            "created_at": utc_now(),
            "project_slug": project_slug,
            "state": dict(snapshot),
        }
        backend = "file_jsonl"
        error = self.error
        if self.available and self._graph is not None:
            try:
                thread_id = str(snapshot.get("langgraph_thread_id") or "moonshine-research-%s" % project_slug)
                self._graph.invoke(
                    dict(snapshot),
                    config={"configurable": {"thread_id": thread_id}},
                )
                backend = "langgraph_memory+file_jsonl"
                error = ""
            except Exception as exc:  # pragma: no cover - optional package/API dependent.
                backend = "file_jsonl"
                error = str(exc)
        checkpoint["backend"] = backend
        checkpoint["error"] = error
        append_jsonl(self.paths.project_research_checkpoints_file(project_slug), checkpoint)
        return {
            "backend": backend,
            "error": error,
            "checkpoint_file": str(self.paths.project_research_checkpoints_file(project_slug)),
        }


class ResearchWorkflowManager(object):
    """Manage an adaptive two-stage workflow with project-memory compatibility."""

    def __init__(self, *, paths, provider=None, memory_manager=None, session_store=None, config=None):
        self.paths = paths
        self.provider = provider
        self.memory_manager = memory_manager
        self.session_store = session_store
        self.config = config
        self.checkpoints = LangGraphCheckpointAdapter(paths)
        self.research_index = ResearchIndexStore(paths)
        self.research_log = ResearchLogStore(
            paths,
            knowledge_store=getattr(memory_manager, "knowledge_store", None) if memory_manager is not None else None,
        )

    def _state_path(self, project_slug: str):
        return self.paths.project_research_workflow_file(project_slug)

    def _runtime_state_path(self, project_slug: str):
        return self.paths.project_research_runtime_state_file(project_slug)

    def _research_index_path(self, project_slug: str):
        return self.paths.project_research_index_file(project_slug)

    def _verification_path(self, project_slug: str):
        return self.paths.project_research_verification_file(project_slug)

    def _research_log_path(self, project_slug: str):
        return self.paths.project_research_log_file(project_slug)

    def workflow_state_runtime_path(self, project_slug: str) -> str:
        """Return the runtime-relative path of the saved workflow snapshot."""
        return "projects/%s/memory/research_workflow.json" % project_slug

    def runtime_state_runtime_path(self, project_slug: str) -> str:
        """Return the runtime-relative path of the lightweight state digest."""
        return "projects/%s/memory/research_state.json" % project_slug

    def ledger_runtime_path(self, project_slug: str) -> str:
        """Return the runtime-relative path of the research ledger."""
        return "projects/%s/memory/ledger.jsonl" % project_slug

    def research_index_runtime_path(self, project_slug: str) -> str:
        """Return the runtime-relative path of the project-local research index."""
        return "projects/%s/memory/research_index.sqlite" % project_slug

    def verification_runtime_path(self, project_slug: str) -> str:
        """Return the runtime-relative path of compact verification history."""
        return "projects/%s/memory/verification.jsonl" % project_slug

    def _stringify_evidence(self, value: object, limit: int = 260) -> str:
        """Render evidence payloads compactly for controller prompts."""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False)
            except TypeError:
                text = str(value)
        return shorten(text, limit)

    def _recent_conversation_events_text(self, session_id: str, limit: int = 12) -> str:
        """Return compact recent conversation events for controller grounding."""
        if self.session_store is None or not session_id:
            return "(session trace unavailable)"
        rows = self.session_store.get_conversation_events(session_id)
        if not rows:
            return "(none)"
        lines = []
        for item in rows[-limit:]:
            content = self._stringify_evidence(item.get("content") or item.get("payload") or "", limit=300)
            lines.append("- [%s/%s] %s" % (item.get("event_kind", "event"), item.get("role", "?"), content or "(empty)"))
        return "\n".join(lines)

    def _recent_tool_events_text(self, session_id: str, limit: int = 6) -> str:
        """Return compact recent tool results for controller grounding."""
        if self.session_store is None or not session_id:
            return "(session trace unavailable)"
        rows = self.session_store.get_tool_events(session_id)
        if not rows:
            return "(none)"
        lines = []
        for item in rows[-limit:]:
            tool_name = str(item.get("tool") or item.get("name") or "tool")
            arguments = self._stringify_evidence(item.get("arguments") or {}, limit=160)
            output = self._stringify_evidence(item.get("output") or {}, limit=220)
            error = self._stringify_evidence(item.get("error"), limit=160)
            line = "- `%s` args=%s output=%s" % (tool_name, arguments or "{}", output or "{}")
            if error:
                line += " error=%s" % error
            lines.append(line)
        return "\n".join(lines)

    def _recent_provider_rounds_text(self, session_id: str, limit: int = 3) -> str:
        """Return compact recent provider-round snapshots for controller grounding."""
        if self.session_store is None or not session_id:
            return "(session trace unavailable)"
        rows = self.session_store.get_provider_rounds(session_id)
        if not rows:
            return "(none)"
        lines = []
        for item in rows[-limit:]:
            response = dict(item.get("response") or {})
            tool_calls = []
            for call in list(response.get("tool_calls") or []):
                if isinstance(call, dict) and call.get("name"):
                    tool_calls.append(str(call.get("name")))
            last_user = ""
            for message in reversed(list(item.get("messages") or [])):
                if str(message.get("role") or "") == "user":
                    last_user = shorten(str(message.get("content") or ""), 160)
                    break
            response_text = shorten(str(response.get("content") or ""), 180)
            parts = [
                "phase=%s" % str(item.get("phase") or "main"),
                "round=%s" % str(item.get("model_round") or ""),
            ]
            if last_user:
                parts.append("last_user=%s" % last_user)
            if tool_calls:
                parts.append("tool_calls=%s" % ", ".join(tool_calls[:4]))
            if response_text:
                parts.append("assistant=%s" % response_text)
            lines.append("- %s" % " | ".join(parts))
        return "\n".join(lines)

    def _recent_workspace_artifacts_text(self, project_slug: str, limit: int = 8) -> str:
        """Return recently updated workspace artifacts for controller grounding."""
        workspace_dir = self.paths.project_workspace_dir(project_slug)
        if not workspace_dir.exists():
            return "(workspace directory missing)"
        files = []
        for candidate in workspace_dir.rglob("*"):
            try:
                if candidate.is_file():
                    files.append(candidate)
            except OSError:
                continue
        if not files:
            return "(none)"

        def _mtime(path) -> float:
            try:
                return float(path.stat().st_mtime)
            except OSError:
                return 0.0

        files.sort(key=_mtime, reverse=True)
        preview_suffixes = {".md", ".txt", ".json", ".jsonl", ".tex", ".py", ".yaml", ".yml"}
        lines = []
        for path in files[:limit]:
            try:
                relative = path.relative_to(self.paths.home).as_posix()
            except ValueError:
                relative = path.as_posix()
            try:
                size = int(path.stat().st_size)
            except OSError:
                size = 0
            preview = ""
            if path.suffix.lower() in preview_suffixes and size <= 200000:
                preview = shorten(read_text(path).replace("\r", " ").replace("\n", " "), 180)
            line = "- `%s` (%s bytes)" % (relative, size)
            if preview:
                line += ": %s" % preview
            lines.append(line)
        return "\n".join(lines)

    def _read_research_channel_rows(self, project_slug: str, channel: str) -> List[Dict[str, object]]:
        """Read records for one research-log type."""
        canonical = normalize_research_channel_name(channel)
        if not canonical:
            return []
        return [
            item
            for item in self.research_log.records(project_slug)
            if isinstance(item, dict)
            and str(item.get("type") or "research_note") == canonical
        ]

    def _recent_research_log_text(self, project_slug: str, limit: int = 6) -> str:
        """Return recent simplified project research-log records."""
        rows = self.research_log.records(project_slug)[-max(1, int(limit or 6)) :]
        if not rows:
            return "(none)"
        lines = []
        for item in rows:
            lines.append(
                "- [{record_type}] {title}: {content}".format(
                    record_type=str(item.get("type") or "research_note"),
                    title=str(item.get("title") or "(untitled)"),
                    content=self._stringify_evidence(item.get("content") or "", limit=240) or "(empty)",
                )
            )
        return "\n".join(lines)

    def _controller_evidence_package(self, *, state: ResearchWorkflowState, session_id: str) -> str:
        """Assemble compact runtime evidence so the controller does not rely on prose alone."""
        return (
            "Recent conversation events:\n{conversation}\n\n"
            "Recent tool results:\n{tools}\n\n"
            "Recent provider rounds:\n{provider_rounds}\n\n"
            "Recent project research_log records:\n{research_log}\n\n"
            "Recent workspace artifacts:\n{workspace}"
        ).format(
            conversation=self._recent_conversation_events_text(session_id),
            tools=self._recent_tool_events_text(session_id),
            provider_rounds=self._recent_provider_rounds_text(session_id),
            research_log=self._recent_research_log_text(state.project_slug),
            workspace=self._recent_workspace_artifacts_text(state.project_slug),
        )

    def _append_recent_choice(self, values: List[str], item: str, limit: int = 8) -> List[str]:
        """Append one recent skill/tool choice while keeping order and deduplicating."""
        normalized = str(item or "").strip()
        if not normalized:
            return list(values)
        result = [value for value in list(values) if str(value or "").strip() != normalized]
        result.append(normalized)
        return result[-limit:]

    def _claim_hash(self, claim: str) -> str:
        """Return the stable hash used to avoid repeated claim verification."""
        return stable_claim_hash(claim)

    def _register_claim(
        self,
        state: ResearchWorkflowState,
        *,
        claim: str,
        status: str = "active",
        review_status: str = "",
        branch_id: str = "",
        source_id: str = "",
        summary: str = "",
    ) -> str:
        """Record or update one claim in the lightweight branch/claim registry."""
        claim_text = str(claim or "").strip()
        claim_hash = self._claim_hash(claim_text)
        if not claim_hash:
            return ""
        now = utc_now()
        entries = [dict(item) for item in list(state.claim_registry or []) if isinstance(item, dict)]
        kept = [item for item in entries if str(item.get("claim_hash") or "") != claim_hash]
        existing = next((item for item in entries if str(item.get("claim_hash") or "") == claim_hash), {})
        entry = {
            **existing,
            "claim_hash": claim_hash,
            "claim": claim_text or str(existing.get("claim") or ""),
            "status": str(status or existing.get("status") or "active"),
            "review_status": str(review_status or existing.get("review_status") or ""),
            "branch_id": str(branch_id or existing.get("branch_id") or state.active_branch_id or ""),
            "source_id": str(source_id or existing.get("source_id") or ""),
            "summary": shorten(str(summary or existing.get("summary") or ""), 500),
            "updated_at": now,
        }
        if not entry.get("created_at"):
            entry["created_at"] = now
        kept.append(entry)
        state.claim_registry = kept[-64:]
        if str(entry.get("review_status") or "") == "passed" or str(entry.get("status") or "") == "verified":
            state.verified_claim_hashes = self._append_recent_choice(
                list(state.verified_claim_hashes or []),
                claim_hash,
                limit=64,
            )
        return claim_hash

    def _upsert_branch_state(
        self,
        state: ResearchWorkflowState,
        *,
        branch_id: str,
        status: str = "",
        summary: str = "",
        current_focus: str = "",
        current_claim: str = "",
        blocker: str = "",
        source_id: str = "",
    ) -> None:
        """Maintain a compact branch registry for long autonomous research runs."""
        normalized = str(branch_id or "").strip()
        if not normalized:
            return
        existing_rows = [dict(item) for item in list(state.branch_states or []) if isinstance(item, dict)]
        kept = [item for item in existing_rows if str(item.get("branch_id") or "").strip() != normalized]
        prior = next((item for item in existing_rows if str(item.get("branch_id") or "").strip() == normalized), {})
        claim_hash = self._claim_hash(current_claim or str(prior.get("current_claim") or ""))
        branch_state = {
            **prior,
            "branch_id": normalized,
            "status": str(status or prior.get("status") or "active"),
            "summary": shorten(str(summary or prior.get("summary") or ""), 500),
            "current_focus": str(current_focus or prior.get("current_focus") or ""),
            "current_claim": str(current_claim or prior.get("current_claim") or ""),
            "claim_hash": claim_hash or str(prior.get("claim_hash") or ""),
            "blocker": str(blocker or prior.get("blocker") or ""),
            "source_id": str(source_id or prior.get("source_id") or ""),
            "updated_at": utc_now(),
        }
        kept.append(branch_state)
        state.branch_states = kept[-24:]
        state.active_branch_id = normalized

    def _verified_claim_hash_set(self, project_slug: str) -> set:
        """Return verified claim hashes from compact verification history."""
        result = set()
        for item in read_jsonl(self._verification_path(project_slug)):
            if not isinstance(item, dict):
                continue
            if str(item.get("review_status") or "") == "passed" or bool(item.get("passed")):
                marker = str(item.get("claim_hash") or "").strip()
                if marker:
                    result.add(marker)
        return result

    def _verified_verification_key_set(self, project_slug: str) -> set:
        """Return passed verification keys from compact verification history."""
        result = set()
        for item in read_jsonl(self._verification_path(project_slug)):
            if not isinstance(item, dict):
                continue
            if not (str(item.get("review_status") or "") == "passed" or bool(item.get("passed"))):
                continue
            marker = str(item.get("verification_key") or "").strip()
            if marker:
                result.add(marker)
        return result

    def _workspace_file_hash(self, project_slug: str, kind: str) -> str:
        """Hash one canonical workspace file if present."""
        if kind == "problem":
            path = self._problem_draft_path(project_slug)
        elif kind == "verified":
            path = self._verified_blueprint_path(project_slug)
        else:
            path = self._blueprint_draft_path(project_slug)
        return _stable_text_hash(read_text(path))

    def _workspace_hashes(self, project_slug: str) -> Dict[str, str]:
        """Return stable hashes for the canonical workspace files."""
        return {
            "problem": self._workspace_file_hash(project_slug, "problem"),
            "blueprint": self._workspace_file_hash(project_slug, "blueprint"),
            "verified": self._workspace_file_hash(project_slug, "verified"),
        }

    def _resolve_runtime_path(self, relative_path: str) -> Path:
        cleaned = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
        return self.paths.home / cleaned

    def _verification_key(
        self,
        *,
        project_slug: str,
        claim: str,
        scope: str = "",
        proof: str = "",
        blueprint_path: str = "",
        tool_name: str = "",
        verifier_version: str = "verify-v1",
    ) -> str:
        """Hash claim + proof/workspace context so proof changes can be reverified."""
        proof_hash = _stable_text_hash(proof)
        blueprint_hash = ""
        if blueprint_path:
            path = self._resolve_runtime_path(blueprint_path)
            if path.exists():
                blueprint_hash = _stable_text_hash(read_text(path))
        if not blueprint_hash:
            blueprint_hash = self._workspace_file_hash(project_slug, "blueprint")
        evidence_hash = proof_hash or blueprint_hash
        payload = "|".join(
            [
                self._claim_hash(claim),
                str(scope or "intermediate"),
                evidence_hash,
                str(tool_name or "verification"),
                verifier_version,
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20] if payload.strip("|") else ""

    def _append_verification_digest(
        self,
        project_slug: str,
        *,
        claim: str,
        summary: str,
        review_status: str,
        status: str = "",
        branch_id: str = "",
        source_id: str = "",
        source_path: str = "",
        metadata: Optional[Dict[str, object]] = None,
        created_at: str = "",
    ) -> Optional[Dict[str, object]]:
        """Append one compact verification row, deduped by verification key and verdict."""
        claim_text = str(claim or "").strip()
        claim_hash = self._claim_hash(claim_text)
        if not claim_hash:
            return None
        payload = dict(metadata or {})
        normalized_review = str(review_status or payload.get("review_status") or "").strip()
        normalized_status = str(status or payload.get("status") or "").strip()
        verification_key = str(payload.get("verification_key") or "").strip()
        if not verification_key:
            verification_key = self._verification_key(
                project_slug=project_slug,
                claim=claim_text,
                scope=str(payload.get("scope") or ""),
                proof=str(payload.get("proof") or ""),
                blueprint_path=str(payload.get("blueprint_path") or ""),
                tool_name=str(payload.get("tool") or ""),
            )
        row_id = deterministic_slug(
            "verification %s %s" % (verification_key or claim_hash, normalized_review),
            summary or claim_text,
            prefix="verification",
        )
        existing_ids = {
            str(item.get("id") or "")
            for item in read_jsonl(self._verification_path(project_slug))
            if isinstance(item, dict)
        }
        existing_keys = {
            "%s|%s" % (str(item.get("verification_key") or ""), str(item.get("review_status") or ""))
            for item in read_jsonl(self._verification_path(project_slug))
            if isinstance(item, dict)
        }
        if row_id in existing_ids or "%s|%s" % (verification_key, normalized_review) in existing_keys:
            return None
        row = {
            "id": row_id,
            "claim": claim_text,
            "claim_hash": claim_hash,
            "verification_key": verification_key,
            "proof_hash": str(payload.get("proof_hash") or _stable_text_hash(str(payload.get("proof") or ""))),
            "blueprint_hash": str(payload.get("blueprint_hash") or self._workspace_file_hash(project_slug, "blueprint")),
            "verifier_version": str(payload.get("verifier_version") or "verify-v1"),
            "summary": shorten(str(summary or ""), 1200),
            "status": normalized_status,
            "review_status": normalized_review,
            "passed": normalized_review == "passed" or bool(payload.get("passed")),
            "tool": str(payload.get("tool") or ""),
            "scope": str(payload.get("scope") or ""),
            "branch_id": str(branch_id or payload.get("branch_id") or ""),
            "critical_errors": [str(item) for item in list(payload.get("critical_errors") or [])],
            "gaps": [str(item) for item in list(payload.get("gaps") or [])],
            "source_id": str(source_id or payload.get("artifact_id") or ""),
            "source_path": str(source_path or payload.get("content_path") or ""),
            "created_at": created_at or utc_now(),
        }
        append_jsonl(self._verification_path(project_slug), row)
        return row

    def _classify_blocker(self, blocker: str) -> str:
        """Classify blockers so autopilot stops only for truly external decisions."""
        text = str(blocker or "").strip().lower()
        if not text:
            return "none"
        hard_markers = [
            "user decision",
            "human decision",
            "ask user",
            "waiting for user",
            "external data",
            "missing file",
            "credential",
            "api key",
            "permission",
            "approval",
        ]
        if any(marker in text for marker in hard_markers):
            return "hard"
        return "soft"

    def needs_consolidation(self, state: ResearchWorkflowState, *, stagnant_count: int = 0) -> Tuple[bool, str]:
        """Return whether the next autonomous turn should consolidate instead of branching."""
        if int(stagnant_count or 0) >= 2:
            return True, "workflow signature repeated across autonomous turns"
        if int(state.iteration_count or 0) > 0 and int(state.iteration_count or 0) % 6 == 0:
            return True, "periodic long-run state compression"
        if len(list(state.open_questions or [])) + len(list(state.failed_paths or [])) >= 8:
            return True, "many unresolved gaps or failed paths are accumulating"
        if self._classify_blocker(state.blocker) == "soft":
            return True, "soft blocker should be turned into a smaller local plan"
        return False, ""

    def autopilot_policy(self, state: ResearchWorkflowState, *, stagnant_count: int = 0) -> Dict[str, object]:
        """Choose the safe autonomous continuation policy for the next turn."""
        blocker_kind = self._classify_blocker(state.blocker)
        if blocker_kind == "hard":
            return {
                "action": "stop",
                "blocker_kind": blocker_kind,
                "reason": "hard blocker requires user input or external access",
                "should_stop": True,
            }
        consolidate, reason = self.needs_consolidation(state, stagnant_count=stagnant_count)
        if consolidate:
            return {
                "action": "consolidate",
                "blocker_kind": blocker_kind,
                "reason": reason,
                "should_stop": False,
            }
        return {
            "action": "continue",
            "blocker_kind": blocker_kind,
            "reason": "no stability intervention needed",
            "should_stop": False,
        }

    def _unique_archive_target(self, base_dir: Path, source_name: str) -> Path:
        base_dir.mkdir(parents=True, exist_ok=True)
        target = base_dir / source_name
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        index = 1
        while True:
            candidate = base_dir / ("%s_%s%s" % (stem, index, suffix))
            if not candidate.exists():
                return candidate
            index += 1

    def _archive_path(self, path: Path, archive_dir: Path) -> Optional[str]:
        if not path.exists():
            return None
        target = self._unique_archive_target(archive_dir, path.name)
        shutil.move(str(path), str(target))
        return _relative_to_home(self.paths, target)

    def _cleanup_recursive_projects(self, project_slug: str) -> List[str]:
        project_dir = self.paths.project_dir(project_slug)
        nested = project_dir / "projects"
        if not nested.exists():
            return []
        archive_dir = self.paths.project_research_archive_dir(project_slug) / "recursive_projects"
        archived = self._archive_path(nested, archive_dir)
        return [archived] if archived else []

    def _is_version_fragment_doc(self, path: Path, project_slug: str) -> bool:
        canonical = {
            self.paths.project_problem_draft_file(project_slug).resolve(),
            self.paths.project_blueprint_file(project_slug).resolve(),
            self.paths.project_blueprint_verified_file(project_slug).resolve(),
            self.paths.project_rules_file(project_slug).resolve(),
            self.paths.project_agents_file(project_slug).resolve(),
        }
        try:
            resolved = path.resolve()
        except OSError:
            return False
        if resolved in canonical:
            return False
        if path.suffix.lower() != ".md":
            return False
        lowered = path.name.lower()
        stem = path.stem.lower()
        prefix_match = stem.startswith(("problem", "blueprint", "proof_blueprint", "current_problem"))
        version_marker = any(marker in lowered for marker in ["draft", "version", "_v", "-v", "copy", "old", "backup", "fragment"])
        return bool(prefix_match and version_marker)

    def _archive_version_fragments(self, project_slug: str) -> List[str]:
        project_dir = self.paths.project_dir(project_slug)
        archive_dir = self.paths.project_research_archive_dir(project_slug) / "version_fragments"
        candidates: List[Path] = []
        for root in [project_dir, self.paths.project_workspace_dir(project_slug)]:
            if not root.exists():
                continue
            for path in root.glob("*.md"):
                if self._is_version_fragment_doc(path, project_slug):
                    candidates.append(path)
        archived: List[str] = []
        seen = set()
        for path in candidates:
            marker = str(path.resolve())
            if marker in seen or not path.exists():
                continue
            seen.add(marker)
            archived_path = self._archive_path(path, archive_dir)
            if archived_path:
                archived.append(archived_path)
        return archived

    def ensure_project_migrated(self, project_slug: str) -> Dict[str, object]:
        """Archive leftover structural fragments from older project layouts."""
        if not project_slug or not self.paths.project_dir(project_slug).exists():
            return {"project_slug": project_slug, "skipped": True}
        archived_recursive = self._cleanup_recursive_projects(project_slug)
        archived_versions = self._archive_version_fragments(project_slug)
        summary = {
            "project_slug": project_slug,
            "archived_recursive_projects": archived_recursive,
            "archived_version_fragments": archived_versions,
            "created_at": utc_now(),
        }
        return summary

    def _remember_recent_artifact(self, state: ResearchWorkflowState, record: Dict[str, object]) -> None:
        """Keep a lightweight rolling window of recent research artifacts in the snapshot."""
        compact = {
            "id": str(record.get("id", "")),
            "artifact_type": str(record.get("artifact_type", "")),
            "title": str(record.get("title", "")),
            "summary": str(record.get("summary", "")),
            "stage": str(record.get("stage", "")),
            "focus_activity": str(record.get("focus_activity", "")),
            "status": str(record.get("status", "")),
            "review_status": str(record.get("review_status", "")),
            "content_path": str(record.get("content_path", "")),
            "created_at": str(record.get("created_at", "")),
        }
        recent = [dict(item) for item in list(state.recent_artifacts) if isinstance(item, dict)]
        recent = [item for item in recent if str(item.get("id", "")) != compact["id"]]
        recent.append(compact)
        state.recent_artifacts = recent[-12:]
        artifact_id = str(record.get("id", "")).strip()
        if artifact_id:
            state.recent_memory_ids = self._append_recent_choice(state.recent_memory_ids, artifact_id, limit=12)

    def _artifact_doc_path(self, project_slug: str, record_id: str) -> object:
        """Return the markdown path used for full artifact bodies."""
        return self.paths.project_research_artifacts_dir(project_slug) / ("%s.md" % record_id)

    def _problem_draft_path(self, project_slug: str):
        """Return the formal current-problem draft path."""
        return self.paths.project_problem_draft_file(project_slug)

    def _blueprint_draft_path(self, project_slug: str):
        """Return the working proof-blueprint path."""
        return self.paths.project_blueprint_file(project_slug)

    def _verified_blueprint_path(self, project_slug: str):
        """Return the published verified blueprint path."""
        return self.paths.project_blueprint_verified_file(project_slug)

    def _normalize_section_heading(self, heading: str) -> str:
        """Normalize a markdown heading for loose matching."""
        return re.sub(r"[^a-z0-9]+", " ", str(heading or "").strip().lower()).strip()

    def _response_sections(self, text: str) -> List[Tuple[str, str]]:
        """Return level-two markdown sections from one assistant response."""
        source = str(text or "")
        known_targets = {
            self._normalize_section_heading(alias)
            for aliases in SECTION_ALIASES.values()
            for alias in aliases
        }
        matches = [
            match
            for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", source)
            if self._normalize_section_heading(str(match.group(1) or "")) in known_targets
        ]
        sections: List[Tuple[str, str]] = []
        if not matches:
            return sections
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            body = source[start:end].strip()
            if body:
                sections.append((str(match.group(1) or "").strip(), body))
        return sections

    def _section_bodies(self, text: str, alias_key: str) -> List[str]:
        """Return all section bodies whose headings match one logical alias."""
        targets = {self._normalize_section_heading(item) for item in list(SECTION_ALIASES.get(alias_key, []))}
        result = []
        for heading, body in self._response_sections(text):
            if self._normalize_section_heading(heading) in targets:
                result.append(body)
        return result

    def _title_from_block(self, text: str, fallback: str) -> str:
        """Build a compact title from the first meaningful line of a text block."""
        for raw_line in str(text or "").splitlines():
            line = re.sub(r"^[\s#>*`0-9.\-\)\(]+", "", raw_line.strip())
            if line:
                return shorten(line, 180)
        return fallback

    def _normalize_compact_text(self, text: str) -> str:
        """Normalize free text for lightweight equality checks."""
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    def _extract_markdown_subsection(self, text: str, headings: List[str]) -> str:
        """Return the body of the first matching level-two subsection."""
        source = str(text or "")
        targets = {self._normalize_section_heading(item) for item in headings}
        matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", source))
        for index, match in enumerate(matches):
            heading = self._normalize_section_heading(str(match.group(1) or ""))
            if heading not in targets:
                continue
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            return source[start:end].strip()
        return ""

    def _problem_statement_from_draft(self, text: str) -> str:
        """Extract the canonical current-problem statement from a formal problem draft."""
        statement = self._extract_markdown_subsection(text, ["statement", "problem statement"])
        if statement:
            return shorten(statement.strip(), 4000)
        lines = []
        for raw_line in str(text or "").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
        return shorten("\n".join(lines).strip(), 4000)

    def _sync_state_from_canonical_workspace(
        self,
        state: ResearchWorkflowState,
        *,
        capture: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Deterministically reduce canonical workspace changes into durable state."""
        capture = dict(capture or {})
        previous_hashes = dict(state.workspace_hashes or {})
        current_hashes = self._workspace_hashes(state.project_slug)
        changes: Dict[str, object] = {
            "workspace_hashes": current_hashes,
            "active_problem_synced": False,
            "problem_placeholder_skipped": False,
            "blueprint_changed": False,
            "verification_invalidated": False,
        }
        problem_text = read_text(self._problem_draft_path(state.project_slug)).strip()
        problem_is_placeholder = self._problem_draft_is_generated_or_placeholder(problem_text)
        if problem_is_placeholder:
            problem_statement = ""
            changes["problem_placeholder_skipped"] = bool(problem_text)
        else:
            problem_statement = self._problem_statement_from_draft(problem_text)
        if problem_statement:
            review = dict(state.problem_review or _default_problem_review())
            preserve_review = str(review.get("review_status") or "") == "passed"
            previous_problem = state.active_problem
            self._set_active_problem(
                state,
                statement=problem_statement,
                created_at=utc_now(),
                preserve_review=preserve_review,
            )
            changes["active_problem_synced"] = (
                self._normalize_compact_text(previous_problem)
                != self._normalize_compact_text(state.active_problem)
            )
        state.workspace_hashes = current_hashes
        return changes

    def _problem_draft_is_generated_or_placeholder(self, text: str) -> bool:
        """Return True when the current problem draft can be safely regenerated."""
        lowered = str(text or "").strip().lower()
        if not lowered:
            return True
        if AUTO_PROBLEM_DRAFT_MARKER in str(text or ""):
            return True
        return (
            "use this file as the formal current problem statement for the project." in lowered
            or ("# current problem draft" in lowered and "(not selected yet)" in lowered)
        )

    def _workspace_file_can_be_replaced(self, text: str, *, kind: str) -> bool:
        """Return whether a workspace draft is still initial/generated boilerplate."""
        stripped = str(text or "").strip()
        lowered = stripped.lower()
        if kind == "problem":
            return self._problem_draft_is_generated_or_placeholder(stripped)
        if not stripped:
            return True
        if kind == "blueprint":
            return True
        return False

    def _append_workspace_draft(self, target, body: str, *, kind: str, title: str) -> str:
        """Append a durable workspace draft block without discarding prior research text."""
        new_body = str(body or "").strip()
        existing_text = read_text(target)
        if self._workspace_file_can_be_replaced(existing_text, kind=kind):
            atomic_write(target, new_body.rstrip() + "\n")
            return str(target.relative_to(self.paths.home).as_posix())

        rendered = (
            existing_text.rstrip()
            + "\n\n---\n\n"
            + "## %s - %s\n\n" % (title, utc_now())
            + new_body.rstrip()
            + "\n"
        )
        atomic_write(target, rendered)
        return str(target.relative_to(self.paths.home).as_posix())

    def _set_active_problem(
        self,
        state: ResearchWorkflowState,
        *,
        statement: str,
        created_at: str,
        preserve_review: bool = False,
    ) -> None:
        """Replace the active problem and reset the review gate when the target changes."""
        candidate = shorten(str(statement or "").strip(), 4000)
        if not candidate:
            return
        changed = self._normalize_compact_text(candidate) != self._normalize_compact_text(state.active_problem)
        state.active_problem = candidate
        if changed and not preserve_review:
            state.problem_review = _default_problem_review()
            state.quality_scores = _default_quality_scores()
            state.transition_status = {
                "last_attempted_stage": "problem_solving",
                "approved": False,
                "reason": "The active problem changed and needs a fresh problem review before entering problem solving.",
                "updated_at": created_at,
            }

    def _parse_review_status(self, text: str) -> str:
        """Infer review status from a free-form review section."""
        lowered = str(text or "").lower()
        explicit = re.search(r"(?im)^\s*(review status|decision|status)\s*:\s*([^\n]+)$", lowered)
        if explicit:
            candidate = explicit.group(2).strip()
        else:
            candidate = lowered
        if any(token in candidate for token in ["passed", "approved", "accept", "ready for solving", "enter problem solving"]):
            return "passed"
        if any(token in candidate for token in ["failed", "rejected", "not ready", "insufficient"]):
            return "failed"
        if any(token in candidate for token in ["refine", "defer", "pending", "revise"]):
            return "pending"
        return "pending"

    def _coerce_score(self, value: str) -> Optional[float]:
        """Normalize one score value into the [0, 1] range."""
        try:
            number = float(str(value or "").strip().rstrip("%"))
        except (TypeError, ValueError):
            return None
        if number > 1.0 and number <= 100.0:
            number = number / 100.0
        return max(0.0, min(number, 1.0))

    def _parse_quality_scores_from_text(self, text: str) -> Dict[str, object]:
        """Extract quality scores from a free-form problem review block."""
        scores = _default_quality_scores()
        for key, value in re.findall(r"(?im)^\s*[-*]?\s*(impact|feasibility|novelty|richness|overall)\s*:\s*([0-9]+(?:\.[0-9]+)?%?)\s*$", str(text or "")):
            normalized = self._coerce_score(value)
            if normalized is not None:
                scores[str(key).lower()] = normalized
        if scores["overall"] <= 0.0:
            scores["overall"] = (
                0.40 * float(scores["impact"])
                + 0.25 * float(scores["feasibility"])
                + 0.20 * float(scores["novelty"])
                + 0.15 * float(scores["richness"])
            )
        rationale_match = re.search(r"(?im)^\s*(rationale|reason|notes?)\s*:\s*(.+)$", str(text or ""))
        scores["rationale"] = str(rationale_match.group(2)).strip() if rationale_match else shorten(str(text or ""), 800)
        return scores

    def _looks_like_complete_blueprint(self, text: str) -> bool:
        """Compatibility helper for older tests; not used for stage advancement."""
        lowered = str(text or "").lower()
        if len(str(text or "").strip()) < 240:
            return False
        if "## proof" not in lowered:
            return False
        if any(token in lowered for token in ["todo", "placeholder", "sketch only", "outline only"]):
            return False
        return "## statement" in lowered or "# theorem" in lowered or "# lemma" in lowered

    def _write_problem_draft(self, state: ResearchWorkflowState, explicit_body: str = "") -> str:
        """Append the current formal problem draft into the workspace."""
        target = self._problem_draft_path(state.project_slug)
        body = str(explicit_body or "").strip()
        if body:
            return self._append_workspace_draft(target, body, kind="problem", title="Problem Draft Update")
        existing_text = read_text(target)
        if existing_text.strip() and not self._problem_draft_is_generated_or_placeholder(existing_text):
            return str(target.relative_to(self.paths.home).as_posix())
        if not body:
            review = dict(state.problem_review or _default_problem_review())
            scores = dict(review.get("quality_scores") or state.quality_scores or _default_quality_scores())
            body = (
                AUTO_PROBLEM_DRAFT_MARKER
                + "\n# Current Problem Draft\n\n"
                "## Statement\n"
                "{statement}\n\n"
                "## Review Status\n"
                "- Status: {status}\n"
                "- Impact: {impact:.2f}\n"
                "- Feasibility: {feasibility:.2f}\n"
                "- Novelty: {novelty:.2f}\n"
                "- Richness: {richness:.2f}\n"
                "- Overall: {overall:.2f}\n\n"
                "## Notes\n"
                "{notes}\n"
            ).format(
                statement=state.active_problem or "(not selected yet)",
                status=str(review.get("review_status") or "not_reviewed"),
                impact=float(scores.get("impact", 0.0) or 0.0),
                feasibility=float(scores.get("feasibility", 0.0) or 0.0),
                novelty=float(scores.get("novelty", 0.0) or 0.0),
                richness=float(scores.get("richness", 0.0) or 0.0),
                overall=float(scores.get("overall", 0.0) or 0.0),
                notes=str(review.get("summary") or state.last_summary or "(none yet)"),
            )
        return self._append_workspace_draft(target, body, kind="problem", title="Problem Draft Update")

    def _write_blueprint_draft(self, project_slug: str, blueprint_body: str) -> str:
        """Compatibility no-op: blueprint.md is now a readable research-log mirror."""
        return str(self._blueprint_draft_path(project_slug).relative_to(self.paths.home).as_posix())

    def _scratchpad_path(self, project_slug: str):
        """Return the path to the active research scratchpad."""
        return self.paths.project_scratchpad_file(project_slug)

    def _write_scratchpad(self, project_slug: str, scratchpad_body: str) -> str:
        """Compatibility no-op: scratchpad.md is no longer maintained by research mode."""
        return str(self._scratchpad_path(project_slug).relative_to(self.paths.home).as_posix())

    def _publish_verified_blueprint(self, project_slug: str) -> str:
        """Copy the readable research log to the verified blueprint path for compatibility."""
        blueprint_text = read_text(self._blueprint_draft_path(project_slug)).strip()
        if not blueprint_text:
            return ""
        target = self._verified_blueprint_path(project_slug)
        atomic_write(target, blueprint_text.rstrip() + "\n")
        return str(target.relative_to(self.paths.home).as_posix())

    def _workspace_file_excerpt(
        self,
        project_slug: str,
        kind: str,
        token_budget: int = 3200,
        limit: Optional[int] = None,
    ) -> str:
        """Return raw workspace text when it fits; never hard-truncate it."""
        if limit is not None:
            token_budget = limit
        if kind == "problem":
            path = self._problem_draft_path(project_slug)
        elif kind == "verified":
            path = self._verified_blueprint_path(project_slug)
        else:
            path = self._blueprint_draft_path(project_slug)
        if not path.exists():
            return "(missing)"
        text = read_text(path).strip()
        if not text:
            return "(empty)"
        if estimate_token_count(text) <= max(1, int(token_budget)):
            return text
        relative = str(path.relative_to(self.paths.home).as_posix())
        return (
            "({kind} exists at `{path}` but exceeds the automatic per-document context budget; "
            "do not inject a truncated excerpt. Read or retrieve the original file content on demand.)"
        ).format(kind=kind, path=relative)

    def _workspace_packet_excerpt(
        self,
        project_slug: str,
        kind: str,
        *,
        full_token_budget: int,
        excerpt_char_budget: int,
        prefer_tail: bool = False,
    ) -> str:
        """Return a raw workspace packet with the full text when feasible, else a high-signal excerpt."""
        if kind == "problem":
            path = self._problem_draft_path(project_slug)
        elif kind == "verified":
            path = self._verified_blueprint_path(project_slug)
        else:
            path = self._blueprint_draft_path(project_slug)
        if not path.exists():
            return "(missing)"
        text = read_text(path).strip()
        if not text:
            return "(empty)"
        if estimate_token_count(text) <= max(1, int(full_token_budget)):
            return text
        trimmed = text[-excerpt_char_budget:] if prefer_tail else text[:excerpt_char_budget]
        label = "tail excerpt" if prefer_tail else "leading excerpt"
        relative = str(path.relative_to(self.paths.home).as_posix())
        return (
            "({label} from `{path}`; the full file is longer and remains canonical.)\n"
            "{excerpt}"
        ).format(
            label=label,
            path=relative,
            excerpt=trimmed.strip(),
        )

    def _channel_entry_raw_content(self, project_slug: str, item: Dict[str, object]) -> str:
        """Resolve one channel row back to its original persisted content whenever possible."""
        metadata = dict(item.get("metadata") or {})
        content_path = str(metadata.get("content_path") or "").strip()
        if content_path:
            path = self.paths.home / content_path
            if path.exists():
                return read_text(path).strip()
        details = str(metadata.get("details") or "").strip()
        if details:
            return details
        return str(item.get("content") or "").strip()

    def _navigation_memory_brief(
        self,
        project_slug: str,
        stage: str,
        limit_per_channel: int = 1,
        token_budget: int = 900,
    ) -> str:
        """Return a compact research-log index; original content is retrieved on demand."""
        type_descriptions = {
            "problem": "researched problems, candidate problems, problem revisions, and final problem statements",
            "verified_conclusion": "verified conclusions, including verified intermediate conclusions",
            "verification": "verification processes, verdicts, gaps, passes, and failures",
            "final_result": "final results, final theorems, and final answers",
            "counterexample": "counterexamples or constructions that refute a claim",
            "failed_path": "failed routes or methods, whether or not a counterexample was also found",
            "research_note": "all other useful research progress, calculations, plans, attempts, and observations",
        }
        rows_by_type: Dict[str, List[Dict[str, object]]] = {record_type: [] for record_type in RESEARCH_LOG_TYPES}
        for row in self.research_log.records(project_slug):
            if not isinstance(row, dict):
                continue
            record_type = str(row.get("type") or "research_note")
            if record_type not in rows_by_type:
                record_type = "research_note"
            rows_by_type[record_type].append(row)
        lines: List[str] = []
        used_tokens = 0
        for record_type in RESEARCH_LOG_TYPES:
            rows = rows_by_type.get(record_type) or []
            if not rows:
                continue
            latest_rows = rows[-max(1, int(limit_per_channel)) :]
            latest_labels: List[str] = []
            for item in reversed(latest_rows):
                label = (
                    str(item.get("title") or "").strip()
                    or str(item.get("id") or "").strip()
                    or "recorded"
                )
                latest_labels.append(label)
            line = "- `{record_type}`: {description} | {count} record(s) | latest: {latest}".format(
                record_type=record_type,
                description=type_descriptions.get(record_type, "research-log records"),
                count=len(rows),
                latest=", ".join(latest_labels) or "recorded",
            )
            line_tokens = estimate_token_count(line)
            if lines and used_tokens + line_tokens > max(1, int(token_budget)):
                continue
            if not lines and line_tokens > max(1, int(token_budget)):
                continue
            lines.append(line)
            used_tokens += line_tokens
        if not lines:
            return (
                "- No project research_log records are populated yet.\n"
                "- New project memory is stored in `projects/%s/memory/research_log.jsonl` and indexed by `projects/%s/memory/research_log_index.sqlite`."
                % (project_slug, project_slug)
            )
        lines.append(
            "- Use `query_memory` to retrieve project memory from `memory/research_log_index.sqlite`; pass `types=[\"failed_path\"]`, `types=[\"verified_conclusion\"]`, or another research-log type only when the need is type-specific."
        )
        lines.append(
            "- `research_log.jsonl` is the project-memory source of truth; `by_type/*.md` files are readable views and the SQLite index is rebuildable."
        )
        return "\n".join(lines)

    def _artifact_channel_for_type(self, artifact_type: str) -> str:
        """Map a research artifact type into the compatible append-only channel when possible."""
        mapping = {
            "candidate_problem": "events",
            "problem_review": "big_decisions",
            "active_problem": "big_decisions",
            "stage_transition": "events",
            "example": "toy_examples",
            "counterexample": "counterexamples",
            "special_case_check": "special_case_checks",
            "novelty_note": "novelty_notes",
            "subgoal_plan": "subgoals",
            "solve_attempt": "solve_steps",
            "lemma_candidate": "immediate_conclusions",
            "conclusion": "immediate_conclusions",
            "verification_report": "verification_reports",
            "failed_path": "failed_paths",
            "branch_update": "branch_states",
            "decision": "big_decisions",
            "checkpoint": "events",
            "note": "events",
            "artifact": "events",
        }
        return mapping.get(str(artifact_type or "").strip(), "events")

    def _artifact_default_activity(self, artifact_type: str, stage: str, current_activity: str) -> str:
        """Infer a focus activity for persisted artifacts when the caller does not provide one."""
        mapping = {
            "candidate_problem": "problem_generation",
            "problem_review": "quality_evaluation",
            "active_problem": "design_checkpoint",
            "stage_transition": "design_checkpoint" if stage == "problem_design" else "persistence",
            "example": "solver_branching" if stage == "problem_solving" else "cross_domain_explore",
            "counterexample": "solver_branching" if stage == "problem_solving" else "problem_refinement",
            "special_case_check": "solver_branching" if stage == "problem_solving" else "quality_evaluation",
            "novelty_note": "persistence" if stage == "problem_solving" else "design_checkpoint",
            "subgoal_plan": "problem_decomposition",
            "solve_attempt": "solver_branching",
            "lemma_candidate": "lemma_extraction",
            "conclusion": "lemma_extraction",
            "verification_report": "pessimistic_verification",
            "failed_path": "correction" if stage == "problem_solving" else "problem_refinement",
            "branch_update": "persistence" if stage == "problem_solving" else "design_checkpoint",
            "decision": current_activity,
            "checkpoint": current_activity,
            "note": current_activity,
            "artifact": current_activity,
        }
        return _safe_activity(mapping.get(str(artifact_type or "").strip(), current_activity), stage)

    def _activity_for_skill_slug(self, skill_slug: str, stage: str, current_activity: str) -> str:
        """Map one loaded research skill into a same-stage focus activity when possible."""
        activity = str(SKILL_ACTIVITY_HINTS.get(str(skill_slug or "").strip(), "")).strip()
        if not activity:
            return ""
        if _stage_for_activity(activity) != _safe_stage(stage):
            return ""
        return _safe_activity(activity, stage or current_activity)

    def _recent_record_signature_exists(self, project_slug: str, signature: str, limit: int = 30) -> bool:
        """Return True when a recent research artifact already carries the same signature."""
        marker = str(signature or "").strip()
        if not marker:
            return False
        rows = [dict(item) for item in self.research_log.records(project_slug) if isinstance(item, dict)]
        for item in reversed(rows[-limit:]):
            if str(item.get("tool_signature") or "").strip() == marker:
                return True
            if str(item.get("auto_capture_signature") or "").strip() == marker:
                return True
        return False

    def _recent_tool_signature_exists(self, project_slug: str, signature: str, limit: int = 30) -> bool:
        """Return True when a recent research artifact already carries the same tool signature."""
        return self._recent_record_signature_exists(project_slug, signature, limit=limit)

    def _record_tool_generated_artifact(
        self,
        *,
        project_slug: str,
        session_id: str,
        state: ResearchWorkflowState,
        tool_name: str,
        artifact_type: str,
        title: str,
        summary: str,
        content: str = "",
        focus_activity: str = "",
        status: str = "recorded",
        review_status: str = "not_applicable",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, object]] = None,
        signature: str = "",
    ) -> Optional[Dict[str, object]]:
        """Persist a deduplicated navigation artifact derived from one real tool result."""
        if not str(title or "").strip() or not str(summary or "").strip():
            return None
        if self._recent_tool_signature_exists(project_slug, signature):
            return None
        tool_tags = ["tool-generated", str(tool_name or "").strip()]
        for item in list(tags or []):
            text = str(item or "").strip()
            if text and text not in tool_tags:
                tool_tags.append(text)
        payload = dict(metadata or {})
        payload["tool_name"] = str(tool_name or "").strip()
        if signature:
            payload["tool_signature"] = signature
        return self.record_artifact(
            project_slug=project_slug,
            session_id=session_id,
            artifact_type=artifact_type,
            title=title,
            summary=summary,
            content=content,
            stage=state.stage,
            focus_activity=focus_activity or state.node,
            status=status,
            review_status=review_status,
            tags=tool_tags,
            metadata=payload,
        )

    def _tool_query_memory_artifact(self, arguments: Dict[str, object], output: Dict[str, object]) -> Optional[Dict[str, object]]:
        """Build one navigation note from a successful query_memory result."""
        query = str(arguments.get("query") or output.get("query") or "").strip()
        compressed = [dict(item) for item in list(output.get("compressed_windows") or []) if isinstance(item, dict)]
        if not compressed:
            return None
        summary = str(output.get("summary") or "").strip()
        if not summary:
            summary = shorten("\n".join(str(item.get("summary") or item.get("title") or "") for item in compressed[:3]), 240)
        if not summary:
            return None
        scope = "all-projects" if bool(arguments.get("all_projects") or output.get("all_projects")) else str(output.get("project_scope") or arguments.get("project_slug") or "current-project")
        source_lines = []
        for item in compressed[:3]:
            source_lines.append(
                "- [%s] %s\n  %s"
                % (
                    str(item.get("source") or "memory"),
                    str(item.get("title") or "retrieved context"),
                    shorten(str(item.get("summary") or item.get("window_excerpt") or ""), 240),
                )
            )
        content = (
            "Query: %s\n"
            "Project scope: %s\n"
                "Type scope: %s\n"
            "Retrieved windows: %s\n\n"
            "Top sources:\n%s"
        ) % (
            query or "(empty query)",
            scope or "current-project",
            ", ".join(str(item) for item in list(output.get("channels") or arguments.get("channels") or []) if str(item).strip()) or "(default)",
            len(compressed),
            "\n".join(source_lines) or "- (no detailed windows)",
        )
        tag_list = ["retrieval", "query-memory"]
        if bool(arguments.get("all_projects") or output.get("all_projects")):
            tag_list.append("cross-project")
        if list(output.get("channels") or arguments.get("channels") or []):
            tag_list.append("channel-scoped")
        return {
            "artifact_type": "note",
            "title": "Memory retrieval: %s" % shorten(query or "recent context", 120),
            "summary": shorten(summary, 240),
            "content": content,
            "tags": tag_list,
            "metadata": {
                "query": query,
                "project_scope": scope,
                "window_count": len(compressed),
                "source_kinds": _dedupe_strings([item.get("source", "") for item in compressed]),
            },
            "signature": "query-memory|%s|%s" % (scope, self._normalize_compact_text(query)),
        }

    def _tool_search_knowledge_artifact(self, arguments: Dict[str, object], output: Dict[str, object]) -> Optional[Dict[str, object]]:
        """Build one navigation note from a successful search_knowledge result."""
        query = str(arguments.get("query") or "").strip()
        rows = [dict(item) for item in list(output.get("results") or []) if isinstance(item, dict)]
        if not rows:
            return None
        lines = []
        for item in rows[:4]:
            lines.append(
                "- %s: %s"
                % (
                    str(item.get("title") or item.get("id") or "knowledge hit"),
                    shorten(str(item.get("statement") or item.get("summary") or ""), 220),
                )
            )
        summary = shorten(
            "Knowledge hits: %s"
            % "; ".join(str(item.get("title") or item.get("id") or "") for item in rows[:3]),
            240,
        )
        return {
            "artifact_type": "note",
            "title": "Knowledge search: %s" % shorten(query or "current topic", 120),
            "summary": summary,
            "content": "Query: %s\nResults: %s\n\nTop hits:\n%s" % (query or "(empty query)", len(rows), "\n".join(lines)),
            "tags": ["retrieval", "knowledge-search"],
            "metadata": {
                "query": query,
                "result_count": len(rows),
                "top_titles": [str(item.get("title") or item.get("id") or "") for item in rows[:4]],
            },
            "signature": "search-knowledge|%s" % self._normalize_compact_text(query),
        }

    def _tool_read_runtime_artifact(
        self,
        *,
        project_slug: str,
        arguments: Dict[str, object],
        output: Dict[str, object],
    ) -> Optional[Dict[str, object]]:
        """Build one navigation note from a high-signal runtime file read."""
        relative_path = str(arguments.get("relative_path") or "").replace("\\", "/").strip().lstrip("./")
        if not relative_path:
            return None
        lower_path = relative_path.lower()
        project_prefix = "projects/%s/" % project_slug.lower()
        high_signal = (
            lower_path.startswith(project_prefix + "references/")
            or lower_path == project_prefix + "workspace/problem.md"
            or lower_path == project_prefix + "workspace/blueprint.md"
            or lower_path == project_prefix + "workspace/blueprint_verified.md"
            or lower_path == project_prefix + "agents.md"
            or lower_path == project_prefix + "rules.md"
            or lower_path == project_prefix + "memory/context.md"
        )
        if not high_signal:
            return None
        content = str(output.get("content") or "").strip()
        if not content:
            return None
        if lower_path.endswith("/workspace/problem.md"):
            title = "Loaded current problem draft"
        elif lower_path.endswith("/workspace/blueprint.md"):
            title = "Loaded readable research log mirror"
        elif lower_path.endswith("/workspace/blueprint_verified.md"):
            title = "Loaded verified blueprint"
        elif "/references/" in lower_path:
            title = "Loaded local reference: %s" % relative_path.split("/")[-1]
        else:
            title = "Loaded project guidance: %s" % relative_path.split("/")[-1]
        summary = shorten(content.replace("\r", " ").replace("\n", " "), 240)
        return {
            "artifact_type": "note",
            "title": title,
            "summary": summary,
            "content": "Path: %s\n\n%s" % (relative_path, shorten(content, 1800)),
            "tags": ["runtime-file", "evidence-load"],
            "metadata": {"relative_path": relative_path},
            "signature": "read-runtime-file|%s" % lower_path,
        }

    def _recent_session_tool_rows(self, session_id: str, limit: int = 8) -> List[Dict[str, object]]:
        """Return the most recent tool-event rows for one session."""
        if self.session_store is None or not session_id:
            return []
        rows = [dict(item) for item in self.session_store.get_tool_events(session_id) if isinstance(item, dict)]
        return rows[-max(1, int(limit)) :]

    def _count_turn_checkpoints(self, project_slug: str, activity: str) -> int:
        """Return legacy turn-refresh checkpoint counts.

        Research mode no longer writes old channel checkpoint events; live attempt
        counters are derived from the current turn's activity only.
        """
        return 0

    def _refresh_live_attempt_counters(self, state: ResearchWorkflowState) -> None:
        """Refresh attempt counters from persisted turn checkpoints plus the current activity."""
        state.correction_attempts = self._count_turn_checkpoints(state.project_slug, "correction")
        state.strengthening_attempts = self._count_turn_checkpoints(state.project_slug, "strengthening")
        if state.node == "correction":
            state.correction_attempts += 1
        if state.node == "strengthening":
            state.strengthening_attempts += 1

    def _refresh_live_state_assessment(self, state: ResearchWorkflowState, *, session_id: str) -> None:
        """Recompute the snapshot assessment from current persisted evidence."""
        tool_rows = self._recent_session_tool_rows(session_id, limit=8)
        tool_names = [
            str(item.get("tool") or item.get("name") or "").strip()
            for item in tool_rows
            if str(item.get("tool") or item.get("name") or "").strip()
        ]
        retrieval_tools = {"query_memory", "search_knowledge", "read_runtime_file"}
        retrieval_rows = [item for item in tool_rows if str(item.get("tool") or item.get("name") or "").strip() in retrieval_tools]
        retrieval_count = len(retrieval_rows)
        latest_artifact = dict(state.recent_artifacts[-1]) if list(state.recent_artifacts or []) else {}
        verification = dict(state.verification or _default_verification())
        final_gate = dict(state.final_verification_gate or _default_final_verification_gate())
        spec = ACTIVITY_SPECS[_safe_activity(state.node, state.stage)]

        if retrieval_count >= 4:
            search_sufficiency = "excessive"
        elif retrieval_count >= 1:
            search_sufficiency = "adequate"
        elif state.stage == "problem_design" and not str(state.active_problem or "").strip():
            search_sufficiency = "insufficient"
        elif state.stage == "problem_design" and not list(state.candidate_problems or []):
            search_sufficiency = "insufficient"
        elif state.node in {"literature_scan", "cross_domain_explore"}:
            search_sufficiency = "insufficient"
        else:
            search_sufficiency = "not_relevant" if state.stage == "problem_solving" else "adequate"

        if bool(final_gate.get("ready_for_final_verification")) and str(verification.get("verdict") or "") == "verified":
            reasoning_state = "verified_partial"
        elif bool(final_gate.get("has_complete_answer")) and not bool(final_gate.get("ready_for_final_verification")):
            reasoning_state = "ready_to_verify"
        elif state.node == "strengthening" or int(state.strengthening_attempts or 0) > 0:
            reasoning_state = "stuck"
        elif str(verification.get("verdict") or "") in {"needs_correction", "incorrect"} or state.node == "correction":
            reasoning_state = "repairing"
        elif not str(state.active_problem or "").strip() and state.iteration_count <= 1:
            reasoning_state = "starting"
        elif list(state.pending_verification_items or []) and state.node in {"lemma_extraction", "pessimistic_verification"}:
            reasoning_state = "ready_to_verify"
        elif list(state.failed_paths or []) and state.stage == "problem_solving":
            reasoning_state = "stuck"
        else:
            reasoning_state = "making_progress"

        memory_need = "none"
        if any(
            str(item.get("tool") or item.get("name") or "").strip() == "query_memory"
            and bool(dict(item.get("arguments") or {}).get("all_projects"))
            for item in tool_rows
        ):
            memory_need = "query_all_projects"
        elif search_sufficiency == "insufficient":
            memory_need = "query_project"
        elif str(verification.get("verdict") or "") == "verified" and any(
            str(item.get("artifact_type") or "") in {"conclusion", "lemma_candidate"} for item in list(state.recent_artifacts or [])[-3:]
        ):
            memory_need = "write_knowledge"
        elif state.node in {"design_checkpoint", "persistence", "correction", "strengthening"} or any(
            name.startswith("record_") or name in {"store_conclusion", "add_knowledge"} for name in tool_names
        ):
            memory_need = "write_project"

        if str(verification.get("verdict") or "") in {"needs_correction", "incorrect"} or state.node in {"correction", "strengthening"}:
            risk_level = "high"
        elif list(state.pending_verification_items or []) or list(state.failed_paths or []) or list(state.open_questions or []):
            risk_level = "medium"
        else:
            risk_level = "low"

        focus_parts = [str(state.next_action or spec["goal"]).strip() or str(spec["goal"])]
        if latest_artifact:
            focus_parts.append(
                "Latest artifact: [%s] %s"
                % (
                    str(latest_artifact.get("artifact_type") or "artifact"),
                    str(latest_artifact.get("title") or latest_artifact.get("summary") or ""),
                )
            )
        current_focus = shorten(" ".join(part for part in focus_parts if part), 1500)

        rationale_parts = []
        if tool_names:
            rationale_parts.append("Recent tools: %s." % ", ".join(tool_names[-5:]))
        if retrieval_count:
            rationale_parts.append("Retrieval activity in the last turns looks %s." % search_sufficiency)
        if str(state.active_problem or "").strip():
            rationale_parts.append("Active problem is set.")
        else:
            rationale_parts.append("No stable active problem is set yet.")
        if str(verification.get("verdict") or "") not in {"", "not_checked"}:
            rationale_parts.append("Verification state is %s." % str(verification.get("verdict") or "not_checked"))
        if list(state.pending_verification_items or []):
            rationale_parts.append(
                "Pending verification targets: %s."
                % ", ".join(str(item) for item in list(state.pending_verification_items or [])[:3])
            )
        if list(state.failed_paths or []):
            rationale_parts.append(
                "Known failed paths: %s."
                % "; ".join(str(item) for item in list(state.failed_paths or [])[-2:])
            )
        if state.node == "correction":
            rationale_parts.append("The current focus is repair after proof pressure or verifier objections.")
        if state.node == "strengthening":
            rationale_parts.append("The current focus is changing assumptions or targets after repeated resistance.")

        state.state_assessment = {
            "current_focus": current_focus,
            "search_sufficiency": search_sufficiency,
            "reasoning_state": reasoning_state,
            "memory_need": memory_need,
            "risk_level": risk_level,
            "rationale": shorten(
                " ".join(part for part in rationale_parts if part)
                or "Live assessment derived from the persisted workflow state, recent tool activity, and recent research artifacts.",
                1800,
            ),
        }

    def _quality_scores_from_metadata(self, metadata: Dict[str, object]) -> Dict[str, object]:
        """Normalize quality scores carried in a problem-review artifact."""
        payload = dict(metadata.get("quality_scores") or {})
        for key in ["impact", "feasibility", "novelty", "richness", "overall"]:
            if key not in payload and key in metadata:
                payload[key] = metadata.get(key)
        scores = _default_quality_scores()
        for key in ["impact", "feasibility", "novelty", "richness", "overall"]:
            try:
                scores[key] = float(payload.get(key, scores[key]) or 0.0)
            except (TypeError, ValueError):
                scores[key] = 0.0
        scores["rationale"] = str(payload.get("rationale") or metadata.get("rationale") or "")
        return scores

    def _auto_record_section_artifact(
        self,
        *,
        project_slug: str,
        session_id: str,
        artifact_type: str,
        title: str,
        summary: str,
        content: str,
        stage: str,
        focus_activity: str,
        status: str = "recorded",
        review_status: str = "not_applicable",
        metadata: Optional[Dict[str, object]] = None,
        set_as_active: bool = False,
        next_action: str = "",
        tags: Optional[List[str]] = None,
    ) -> None:
        """Persist one runtime-generated research artifact without exposing a model tool."""
        self.record_artifact(
            project_slug=project_slug,
            session_id=session_id,
            artifact_type=artifact_type,
            title=title,
            summary=summary,
            content=content,
            stage=stage,
            focus_activity=focus_activity,
            status=status,
            review_status=review_status,
            metadata=dict(metadata or {}),
            set_as_active=set_as_active,
            next_action=next_action,
            tags=list(tags or []),
        )

    def _most_recent_matching_skill(self, state: ResearchWorkflowState, skill_slugs: List[str]) -> str:
        """Return the most recent selected skill that matches one of the targets."""
        targets = {str(item or "").strip() for item in list(skill_slugs or []) if str(item or "").strip()}
        for skill_slug in reversed(list(state.selected_skills or [])):
            normalized = str(skill_slug or "").strip()
            if normalized in targets:
                return normalized
        return ""

    def _auto_record_recent_skill_artifact(
        self,
        *,
        project_slug: str,
        session_id: str,
        state: ResearchWorkflowState,
        assistant_message: str,
        artifact_type: str,
        skill_slugs: List[str],
        title_default: str,
        focus_activity: str,
        tags: Optional[List[str]] = None,
        status: str = "recorded",
        review_status: str = "not_applicable",
        metadata: Optional[Dict[str, object]] = None,
        set_as_active: bool = False,
        next_action: str = "",
    ) -> bool:
        """Persist a research artifact from recent skill context without requiring a record_* tool."""
        skill_slug = self._most_recent_matching_skill(state, skill_slugs)
        body = str(assistant_message or "").strip()
        if not skill_slug or not body:
            return False
        normalized = self._normalize_compact_text(body)
        if not normalized:
            return False
        signature = "skill-auto|%s|%s|%s" % (
            artifact_type,
            skill_slug,
            normalized[:400],
        )
        if self._recent_record_signature_exists(project_slug, signature):
            return False
        payload = dict(metadata or {})
        payload["skill_slug"] = skill_slug
        payload["auto_capture_signature"] = signature
        if artifact_type == "novelty_note" and "novelty_axes" not in payload:
            lowered = body.lower()
            novelty_axes = []
            if "new concept" in lowered:
                novelty_axes.append("new_concept")
            if "new method" in lowered:
                novelty_axes.append("new_method")
            if "new theory" in lowered:
                novelty_axes.append("new_theory")
            if novelty_axes:
                payload["novelty_axes"] = novelty_axes
        auto_tags = [str(item) for item in list(tags or []) if str(item).strip()]
        if "skill-auto" not in auto_tags:
            auto_tags.append("skill-auto")
        if skill_slug not in auto_tags:
            auto_tags.append(skill_slug)
        self.record_artifact(
            project_slug=project_slug,
            session_id=session_id,
            artifact_type=artifact_type,
            title=self._title_from_block(body, title_default),
            summary=shorten(body, 240),
            content=body,
            stage=state.stage,
            focus_activity=focus_activity,
            status=status,
            review_status=review_status,
            metadata=payload,
            set_as_active=set_as_active,
            next_action=next_action,
            tags=auto_tags,
        )
        return True

    def _capture_turn_progress(
        self,
        *,
        project_slug: str,
        session_id: str,
        state: ResearchWorkflowState,
        assistant_message: str,
    ) -> Dict[str, object]:
        """Capture direct stage proposals from assistant output.

        Project research memory is now archived by the independent research-log
        pass after the turn. This capture step deliberately avoids writing
        project drafts from assistant sections.
        """
        capture = {
            "updated_files": [],
            "returned_to_design": False,
            "auto_recorded": [],
            "workspace_events": [],
        }
        message = str(assistant_message or "")
        if not message.strip():
            return capture

        transition_blocks = self._section_bodies(message, "stage_transition")
        if transition_blocks:
            state = self.load_state(project_slug)
            transition_reduction = self._sync_state_from_canonical_workspace(state, capture=capture)
            if transition_reduction.get("active_problem_synced") or transition_reduction.get("verification_invalidated"):
                self.save_state(state, mirror_progress=False, checkpoint_reason="pre_stage_transition_workspace_sync")
            state = self.load_state(project_slug)
            block = transition_blocks[-1]
            lowered = block.lower()
            if "problem_design" in lowered or "return to design" in lowered or "reformulat" in lowered:
                target_stage = "problem_design"
            elif "problem_solving" in lowered or "enter solving" in lowered or "move to solving" in lowered:
                target_stage = "problem_solving"
            else:
                target_stage = ""
            if target_stage:
                self._apply_stage_transition(
                    state,
                    metadata={"target_stage": target_stage, "reason": shorten(block, 600)},
                    created_at=utc_now(),
                    summary=shorten(block, 240),
                )
                self.save_state(state, mirror_progress=False, checkpoint_reason="stage_transition_section")

        return capture

    def can_enter_problem_solving(self, state: ResearchWorkflowState) -> Tuple[bool, str]:
        """Return whether the current research snapshot supports entering problem solving."""
        if not str(state.active_problem or "").strip():
            return False, "No active problem has been selected yet."
        review = dict(state.problem_review or _default_problem_review())
        if str(review.get("review_status") or "not_reviewed") != "passed":
            return (
                False,
                "The active problem has not yet passed a dedicated quality-assessor review. "
                "Load the `quality-assessor` skill, call `assess_problem_quality` once, and only then enter problem solving.",
            )
        metadata = dict(review.get("metadata") or {})
        skill_slug = str(metadata.get("skill_slug") or "")
        if skill_slug != "quality-assessor":
            return (
                False,
                "The active problem review was not produced by the dedicated quality-assessor gate. "
                "Load the `quality-assessor` skill and call `assess_problem_quality` once before entering problem solving.",
            )
        scores = dict(review.get("quality_scores") or state.quality_scores or _default_quality_scores())
        try:
            overall = float(scores.get("overall", 0.0) or 0.0)
        except (TypeError, ValueError):
            overall = 0.0
        if overall < 0.55:
            return False, "The current problem review score is below the solving threshold."
        return True, "The active problem has a passed quality review and can enter problem solving."

    def _compact_turn_summary(self, assistant_message: str) -> str:
        """Extract one compact turn summary instead of replaying a full status memo."""
        message = str(assistant_message or "").strip()
        if not message:
            return ""
        for raw_line in message.splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            lowered = line.lower()
            if line.startswith("#"):
                continue
            if lowered in {"status", "decision rule", "blockers and mitigations", "files i will update next"}:
                continue
            if lowered.startswith(("status ", "status:", "one-line state", "short status", "immediate next actions")):
                continue
            return shorten(line, 320)
        collapsed = " ".join(part.strip() for part in message.splitlines() if part.strip())
        return shorten(collapsed, 320)

    def _find_recent_verification_match(self, *, project_slug: str, text: str) -> Optional[Dict[str, object]]:
        """Find a recent passed verification record that plausibly matches the target text."""
        rows = [dict(item) for item in self.research_log.records(project_slug) if isinstance(item, dict)]
        target = str(text or "").strip()
        if not target:
            return None
        best = None
        best_score = 0.0
        for item in reversed(rows[-40:]):
            if str(item.get("type") or "") != "verification":
                continue
            blob = "\n".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("content") or ""),
                ]
            )
            score = overlap_score(target, blob)
            if score > best_score:
                best = item
                best_score = score
        return best if best_score >= 0.2 else None

    def check_conclusion_gate(self, *, project_slug: str, title: str, statement: str, status: str) -> Tuple[bool, str]:
        """Return whether a conclusion may be promoted into formal knowledge."""
        normalized_status = str(status or "").strip()
        if normalized_status != "verified":
            return False, "Only verified conclusions may enter formal knowledge; keep this one as a project candidate."
        claim_hash = self._claim_hash("%s\n%s" % (title, statement))
        statement_hash = self._claim_hash(statement)
        verified_hashes = self._verified_claim_hash_set(project_slug)
        if claim_hash in verified_hashes or statement_hash in verified_hashes:
            return True, "A passed verification digest with the same claim hash supports promoting this conclusion."
        match = self._find_recent_verification_match(project_slug=project_slug, text="%s\n%s" % (title, statement))
        if match is None:
            return False, "No passed verification artifact matches this conclusion yet."
        return True, "A passed verification artifact supports promoting this conclusion."

    def _update_problem_review(self, state: ResearchWorkflowState, *, title: str, summary: str, review_status: str, metadata: Dict[str, object], created_at: str) -> None:
        """Refresh the current problem-review snapshot from one persisted artifact."""
        scores = self._quality_scores_from_metadata(metadata)
        state.problem_review = {
            "title": title,
            "summary": summary,
            "review_status": review_status,
            "passed": review_status == "passed",
            "quality_scores": scores,
            "metadata": dict(metadata or {}),
            "updated_at": created_at,
        }
        state.quality_scores = dict(scores)

    def _apply_stage_transition(self, state: ResearchWorkflowState, *, metadata: Dict[str, object], created_at: str, summary: str) -> None:
        """Apply a stage-transition proposal if the snapshot satisfies the required state."""
        target_stage = _safe_stage(str(metadata.get("target_stage") or metadata.get("stage") or ""))
        if not target_stage:
            return
        if target_stage == "problem_solving":
            approved, reason = self.can_enter_problem_solving(state)
            state.transition_status = {
                "last_attempted_stage": target_stage,
                "approved": approved,
                "reason": reason,
                "updated_at": created_at,
            }
            if approved:
                state.stage = "problem_solving"
                state.node = "problem_decomposition"
                state.next_action = str(metadata.get("next_action") or "Decompose the selected problem into subgoals.").strip()
                state.last_summary = summary or reason
            else:
                state.open_questions = _dedupe_strings(state.open_questions + ["Problem-solving transition blocked: %s" % reason])
        elif target_stage == "problem_design":
            state.stage = "problem_design"
            state.node = "problem_refinement"
            state.transition_status = {
                "last_attempted_stage": target_stage,
                "approved": True,
                "reason": str(metadata.get("reason") or "Returned to problem design for reformulation."),
                "updated_at": created_at,
            }
            state.next_action = str(metadata.get("next_action") or "Refine or reformulate the current problem.").strip()
        if str(metadata.get("mark_completed") or "").lower() in {"true", "1", "yes"} and bool(state.final_verification_gate.get("ready_for_final_verification")):
            state.status = "completed"

    def _apply_artifact_to_state(self, state: ResearchWorkflowState, record: Dict[str, object]) -> Dict[str, object]:
        """Update the current research snapshot from one append-only research artifact."""
        artifact_type = str(record.get("artifact_type") or "")
        title = str(record.get("title") or "")
        summary = str(record.get("summary") or "")
        content = str(record.get("content_inline") or "")
        status = str(record.get("status") or "recorded")
        review_status = str(record.get("review_status") or "not_applicable")
        metadata = dict(record.get("metadata") or {})
        created_at = str(record.get("created_at") or utc_now())
        focus_activity = str(record.get("focus_activity") or "")
        applied = {"stage_changed": False, "approved": True, "reason": ""}

        if str(record.get("stage") or "").strip():
            state.stage = _safe_stage(str(record.get("stage") or state.stage))
        if focus_activity.strip():
            state.node = _safe_activity(focus_activity, state.stage)
        else:
            state.node = self._artifact_default_activity(artifact_type, state.stage, state.node)
        branch_id = str(metadata.get("branch_id") or "").strip()
        if branch_id:
            state.active_branch_id = branch_id
        current_focus = str(metadata.get("current_focus") or "").strip()
        if current_focus:
            state.current_focus = current_focus
        current_claim = str(metadata.get("current_claim") or metadata.get("claim") or "").strip()
        if current_claim:
            state.current_claim = current_claim
            self._register_claim(
                state,
                claim=current_claim,
                status=status or artifact_type,
                review_status=review_status,
                branch_id=state.active_branch_id,
                source_id=str(record.get("id") or ""),
                summary=summary or title,
            )
        blocker = str(metadata.get("blocker") or "").strip()
        if blocker:
            state.blocker = blocker
        if state.active_branch_id and (summary or current_claim or blocker):
            self._upsert_branch_state(
                state,
                branch_id=state.active_branch_id,
                status=status or "updated",
                summary=summary or title,
                current_focus=state.current_focus,
                current_claim=state.current_claim,
                blocker=state.blocker,
                source_id=str(record.get("id") or ""),
            )

        if artifact_type == "candidate_problem":
            candidate = {
                "id": str(record.get("id") or ""),
                "title": title,
                "statement": content or summary,
                "rationale": summary,
                "status": status,
                "review_status": review_status,
            }
            state.candidate_problems = [item for item in list(state.candidate_problems) if str(item.get("id", "")) != candidate["id"]]
            state.candidate_problems.append(candidate)
            state.candidate_problems = state.candidate_problems[-12:]
            if bool(record.get("set_as_active")):
                self._set_active_problem(
                    state,
                    statement=str(metadata.get("statement") or content or title or summary),
                    created_at=created_at,
                    preserve_review=False,
                )
        elif artifact_type == "problem_review":
            self._update_problem_review(
                state,
                title=title,
                summary=summary,
                review_status=review_status,
                metadata=metadata,
                created_at=created_at,
            )
            if bool(record.get("set_as_active")) and str(metadata.get("statement") or content).strip():
                self._set_active_problem(
                    state,
                    statement=str(metadata.get("statement") or content),
                    created_at=created_at,
                    preserve_review=True,
                )
        elif artifact_type == "active_problem":
            self._set_active_problem(
                state,
                statement=str(metadata.get("statement") or content or title or summary),
                created_at=created_at,
                preserve_review=False,
            )
        elif artifact_type == "stage_transition":
            previous_stage = state.stage
            self._apply_stage_transition(state, metadata=metadata, created_at=created_at, summary=summary)
            applied["stage_changed"] = previous_stage != state.stage
            applied["approved"] = bool((state.transition_status or {}).get("approved", True))
            applied["reason"] = str((state.transition_status or {}).get("reason", ""))
        elif artifact_type == "verification_report":
            full_verification = bool(metadata.get("full_verification"))
            if review_status == "failed":
                verdict = "needs_correction"
            elif review_status == "passed" and full_verification:
                verdict = "verified"
            else:
                verdict = "not_checked"
            critical_errors = list((metadata.get("critical_errors") or []))
            state.verification = {
                "verdict": verdict,
                "critical_errors": [str(item) for item in critical_errors],
                "rationale": summary,
            }
            verification_claim = str(metadata.get("claim") or current_claim or state.current_claim or title)
            claim_hash = self._register_claim(
                state,
                claim=verification_claim,
                status="verified" if review_status == "passed" else "needs_correction",
                review_status=review_status,
                branch_id=state.active_branch_id,
                source_id=str(record.get("id") or ""),
                summary=summary,
            )
            if claim_hash:
                metadata["claim_hash"] = claim_hash
            targets = [str(item) for item in list(metadata.get("targets") or []) if str(item).strip()]
            if review_status == "failed":
                state.pending_verification_items = _dedupe_strings(targets)
            elif review_status == "passed" and full_verification:
                state.pending_verification_items = []
            scope = str(metadata.get("scope") or "")
            blueprint_path = str(metadata.get("blueprint_path") or "")
            if scope == "final" and full_verification:
                state.final_verification_gate = {
                    "has_complete_answer": bool(metadata.get("has_complete_answer", bool(blueprint_path))),
                    "ready_for_final_verification": review_status == "passed",
                    "blueprint_path": blueprint_path,
                    "reason": summary,
                }
                if review_status == "passed":
                    state.status = "completed"
                else:
                    state.status = "active"
        elif artifact_type == "failed_path":
            state.failed_paths = _dedupe_strings(state.failed_paths + [summary or title])
        elif artifact_type == "branch_update":
            branch_id = str(metadata.get("branch_id") or title or "").strip() or "branch"
            branch_status = str(metadata.get("branch_status") or metadata.get("status") or status or "").strip() or "updated"
            self._upsert_branch_state(
                state,
                branch_id=branch_id,
                status=branch_status,
                summary=summary or title,
                current_focus=state.current_focus,
                current_claim=state.current_claim,
                blocker=state.blocker,
                source_id=str(record.get("id") or ""),
            )
        elif artifact_type == "checkpoint":
            state.last_summary = summary
            if str(record.get("next_action") or "").strip():
                state.next_action = str(record.get("next_action") or "").strip()
        elif artifact_type in {"subgoal_plan", "solve_attempt", "lemma_candidate", "conclusion", "decision", "example", "counterexample", "special_case_check", "novelty_note", "note", "artifact"}:
            if artifact_type == "failed_path":
                state.failed_paths = _dedupe_strings(state.failed_paths + [summary or title])
            if str(record.get("next_action") or "").strip():
                state.next_action = str(record.get("next_action") or "").strip()
            if artifact_type == "lemma_candidate" and review_status != "passed":
                state.pending_verification_items = _dedupe_strings(state.pending_verification_items + [title or summary])

        if summary:
            state.last_summary = summary
        if str(record.get("next_action") or "").strip():
            state.next_action = str(record.get("next_action") or "").strip()
        elif not str(state.next_action or "").strip():
            state.next_action = summary or title
        self._remember_recent_artifact(state, record)
        return applied

    def record_artifact(
        self,
        *,
        project_slug: str,
        session_id: str,
        artifact_type: str,
        title: str,
        summary: str,
        content: str = "",
        stage: str = "",
        focus_activity: str = "",
        status: str = "recorded",
        review_status: str = "not_applicable",
        related_ids: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        next_action: str = "",
        set_as_active: bool = False,
        metadata: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Deprecated explicit artifact entry point.

        Project research memory is managed only by the post-turn archival pass.
        """
        return {
            "id": "",
            "artifact_type": str(artifact_type or "").strip(),
            "title": str(title or "").strip(),
            "stage": str(stage or ""),
            "focus_activity": str(focus_activity or ""),
            "status": "deprecated",
            "content_path": "",
            "summary": str(summary or ""),
            "archived": 0,
            "message": "Explicit artifact recording is disabled; the archival pass manages research_log.jsonl.",
        }

    def commit_turn(
        self,
        *,
        project_slug: str,
        session_id: str,
        title: str,
        summary: str,
        next_action: str = "",
        stage: str = "",
        focus_activity: str = "",
        status: str = "",
        branch_id: str = "",
        current_focus: str = "",
        current_claim: str = "",
        blocker: str = "",
        problem_draft: str = "",
        blueprint_draft: str = "",
        scratchpad: str = "",
        open_questions: Optional[List[str]] = None,
        failed_paths: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        related_ids: Optional[List[str]] = None,
    ) -> Dict[str, object]:
        """Commit one durable research turn back into the canonical workspace and lightweight state."""
        state = self.load_state(project_slug)
        updated_files: List[str] = []
        created_at = utc_now()
        if str(stage or "").strip():
            state.stage = _safe_stage(stage)
        if str(focus_activity or "").strip():
            state.node = _safe_activity(focus_activity, state.stage)
        if str(status or "").strip():
            state.status = str(status or "").strip()
        if str(branch_id or "").strip():
            state.active_branch_id = str(branch_id or "").strip()
        if str(current_focus or "").strip():
            state.current_focus = str(current_focus or "").strip()
        if str(current_claim or "").strip():
            state.current_claim = str(current_claim or "").strip()
        if str(blocker or "").strip():
            state.blocker = str(blocker or "").strip()
        if str(summary or "").strip():
            state.last_summary = str(summary or "").strip()
        if str(next_action or "").strip():
            state.next_action = str(next_action or "").strip()
        claim_hash = ""
        if str(state.current_claim or "").strip():
            claim_hash = self._register_claim(
                state,
                claim=state.current_claim,
                status="active",
                branch_id=state.active_branch_id,
                summary=summary,
            )
        if str(state.active_branch_id or "").strip():
            self._upsert_branch_state(
                state,
                branch_id=state.active_branch_id,
                status=status or "active",
                summary=summary,
                current_focus=state.current_focus,
                current_claim=state.current_claim,
                blocker=state.blocker,
            )
        if open_questions:
            state.open_questions = _dedupe_strings(list(open_questions) + list(state.open_questions), limit=20)
        if failed_paths:
            state.failed_paths = _dedupe_strings(list(failed_paths) + list(state.failed_paths), limit=20)
        # problem_draft, blueprint_draft, and scratchpad are accepted for backward
        # compatibility but do not write canonical research-mode files. Current
        # project memory is archived through research_log.jsonl after each turn.
        workspace_reduction = self._sync_state_from_canonical_workspace(
            state,
            capture={"updated_files": list(updated_files), "source": "commit_turn"},
        )

        entry = {
            "id": deterministic_slug(
                "%s %s %s" % ("turn_commit", str(title or "").strip(), created_at),
                str(summary or "").strip(),
                prefix="turn_commit",
            ),
            "kind": "turn_commit",
            "title": str(title or "").strip() or "Research Turn Commit",
            "summary": str(summary or "").strip(),
            "stage": state.stage,
            "focus_activity": state.node,
            "status": state.status,
            "branch_id": state.active_branch_id,
            "claim": state.current_claim,
            "claim_hash": claim_hash,
            "blocker": state.blocker,
            "next_action": state.next_action,
            "updated_files": list(updated_files),
            "workspace_hashes": self._workspace_hashes(project_slug),
            "workspace_reduction": workspace_reduction,
            "created_at": created_at,
        }
        checkpoint_meta = self.save_state(state, mirror_progress=False, checkpoint_reason="turn_commit")
        if self.session_store is not None:
            self.session_store.append_turn_event(
                session_id,
                {
                    "type": "research_turn_committed",
                    "text": "%s: %s" % (str(entry.get("kind") or "turn_commit"), str(entry.get("title") or "")),
                    "created_at": created_at,
                    "entry": entry,
                    "updated_files": updated_files,
                    "snapshot": state.to_dict(),
                    "checkpoint": checkpoint_meta,
                },
            )
        return {
            "id": str(entry.get("id") or ""),
            "kind": "turn_commit",
            "title": str(entry.get("title") or ""),
            "summary": str(entry.get("summary") or ""),
            "stage": state.stage,
            "focus_activity": state.node,
            "status": state.status,
            "active_branch_id": state.active_branch_id,
            "current_claim": state.current_claim,
            "blocker": state.blocker,
            "next_action": state.next_action,
            "updated_files": updated_files,
            "workspace_reduction": workspace_reduction,
            "checkpoint": checkpoint_meta,
            "snapshot": state.to_dict(),
        }

    def observe_tool_result(
        self,
        *,
        project_slug: str,
        session_id: str,
        tool_name: str,
        arguments: Dict[str, object],
        output: Dict[str, object],
        error: str = "",
    ) -> None:
        """No-op observer.

        Tool events are saved in the session log by the caller. The post-turn
        archival pass reads those events and is the only path that updates
        project research memory.
        """
        return

    def refresh_after_turn(
        self,
        *,
        project_slug: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> Dict[str, object]:
        """Refresh the research snapshot after one main-agent turn from persisted state."""
        prior_state = self.load_state(project_slug, seed=user_message)
        previous_stage = prior_state.stage
        previous_node = prior_state.node
        capture = self._capture_turn_progress(
            project_slug=project_slug,
            session_id=session_id,
            state=prior_state,
            assistant_message=assistant_message,
        )
        state = self.load_state(project_slug, seed=user_message)
        workspace_reduction = self._sync_state_from_canonical_workspace(state, capture=capture)
        capture["workspace_reduction"] = workspace_reduction
        state.iteration_count += 1
        if state.stage == "problem_design":
            state.design_iteration_count += 1
        else:
            state.solving_iteration_count += 1
        if capture.get("returned_to_design"):
            state.stage = "problem_design"
            state.node = "problem_refinement"
            state.transition_status = {
                "last_attempted_stage": "problem_design",
                "approved": True,
                "reason": "The research target changed enough to return to problem design.",
                "updated_at": utc_now(),
            }
            state.next_action = "Refine the active problem and re-evaluate its quality before resuming solving."
        elif state.stage == "problem_design":
            approved, reason = self.can_enter_problem_solving(state)
            current_transition = dict(state.transition_status or _default_transition_status())
            if not str(current_transition.get("reason") or "").strip():
                state.transition_status = {
                    "last_attempted_stage": str(current_transition.get("last_attempted_stage") or ""),
                    "approved": bool(current_transition.get("approved", False)),
                    "reason": reason,
                    "updated_at": utc_now(),
                }
            elif not approved and str(current_transition.get("last_attempted_stage") or "") != "problem_design":
                state.transition_status = {
                    "last_attempted_stage": str(current_transition.get("last_attempted_stage") or ""),
                    "approved": False,
                    "reason": reason,
                    "updated_at": utc_now(),
                }
            if approved and not str(state.next_action or "").strip():
                state.next_action = "Proceed into problem-solving work, or continue refining if the formulation still feels premature."
            elif not approved and "quality-assessor" in reason:
                state.next_action = (
                    "Load the `quality-assessor` skill, call `assess_problem_quality` once for the active problem, "
                    "and stay in problem design until that dedicated review passes."
                )
        state.last_summary = self._compact_turn_summary(assistant_message) or state.last_summary or "Research turn completed."
        if not str(state.next_action or "").strip():
            state.next_action = (
                (
                    "If the active problem is mature, proceed into problem-solving work; otherwise continue refining and reviewing the problem."
                    if state.stage == "problem_design" and self.can_enter_problem_solving(state)[0]
                    else "Continue problem design using the current candidate problems and reviews."
                )
                if state.stage == "problem_design"
                else "Continue problem solving from the current branch, research log, and verification state."
            )
        if not str(state.current_focus or "").strip():
            state.current_focus = (
                "Refine the active problem and its supporting evidence."
                if state.stage == "problem_design"
                else "Advance the active branch and integrate verified mathematical progress."
            )
        if str(state.current_claim or "").strip():
            self._register_claim(
                state,
                claim=state.current_claim,
                status="active",
                branch_id=state.active_branch_id,
                summary=state.last_summary,
            )
        if str(state.active_branch_id or "").strip():
            self._upsert_branch_state(
                state,
                branch_id=state.active_branch_id,
                status=state.status,
                summary=state.last_summary,
                current_focus=state.current_focus,
                current_claim=state.current_claim,
                blocker=state.blocker,
            )
        if not str(state.active_problem or "").strip() and state.candidate_problems:
            latest = dict(state.candidate_problems[-1])
            state.active_problem = str(latest.get("statement") or latest.get("title") or "")
        blueprint_relative = ""
        blueprint_text = ""
        blueprint_path = self._blueprint_draft_path(project_slug)
        if blueprint_path.exists():
            blueprint_relative = str(blueprint_path.relative_to(self.paths.home).as_posix())
            blueprint_text = read_text(blueprint_path)
        if state.final_verification_gate.get("ready_for_final_verification") and not str(state.final_verification_gate.get("blueprint_path") or "").strip():
            state.final_verification_gate = {
                "has_complete_answer": bool(state.final_verification_gate.get("has_complete_answer") or blueprint_relative),
                "ready_for_final_verification": True,
                "blueprint_path": blueprint_relative,
                "reason": str(state.final_verification_gate.get("reason") or "Final verification has passed."),
            }
        self._refresh_live_attempt_counters(state)
        self._refresh_live_state_assessment(state, session_id=session_id)
        checkpoint_meta = self.save_state(state, mirror_progress=False, checkpoint_reason="turn_refresh")
        payload = {
            "previous_stage": previous_stage,
            "stage": state.stage,
            "previous_node": previous_node,
            "current_node": state.node,
            "previous_activity": previous_node,
            "current_activity": state.node,
            "status": state.status,
            "activity_status": "checkpointed",
            "summary": state.last_summary,
            "next_action": state.next_action,
            "active_branch_id": state.active_branch_id,
            "current_claim": state.current_claim,
            "blocker": state.blocker,
            "active_problem": state.active_problem,
            "problem_review": dict(state.problem_review),
            "selected_skills": list(state.selected_skills),
            "selected_tools": list(state.selected_tools),
            "state_assessment": dict(state.state_assessment),
            "failed_paths": list(state.failed_paths),
            "pending_verification_items": list(state.pending_verification_items),
            "final_verification_gate": dict(state.final_verification_gate),
            "correction_attempts": int(state.correction_attempts),
            "strengthening_attempts": int(state.strengthening_attempts),
            "recent_artifacts": list(state.recent_artifacts),
            "transition_status": dict(state.transition_status),
            "capture": dict(capture),
            "updated_files": list(capture.get("updated_files") or []),
            "checkpoint": checkpoint_meta,
        }
        if self.session_store is not None:
            self.session_store.append_turn_event(
                session_id,
                {
                    "type": "research_state_refreshed",
                    "text": "Research workflow snapshot refreshed; project progress is archived separately in research_log.",
                    "created_at": utc_now(),
                    **payload,
                },
            )
        return payload

    def build_autonomous_prompt(
        self,
        state: ResearchWorkflowState,
        *,
        stagnant_count: int = 0,
        previous_signature: str = "",
        autopilot_policy: Optional[Dict[str, object]] = None,
    ) -> str:
        """Build a continuation prompt that keeps autonomous research moving."""
        policy = dict(autopilot_policy or self.autopilot_policy(state, stagnant_count=stagnant_count))
        next_action = str(state.next_action or "").strip()
        blocker = str(state.blocker or "").strip()
        current_focus = str(state.current_focus or "").strip()
        current_claim = str(state.current_claim or "").strip()
        lines = [
            "Continue focusing on the current research progress and advance the next step directly from the multi-turn conversation.",
            "Use the existing conversation history, tool results, and current proof text as the authoritative live state.",
            "Use workspace files and retrieved project materials only when they help the next mathematical move.",
            "",
            "Continuation contract:",
            "- Make one concrete mathematical move: formulate, test, prove, refute, repair, verify, consolidate, or write.",
            "- Do not merely recap prior status.",
            "- If the previous action is blocked, choose a smaller local mathematical step or replan the branch unless an external user decision is truly required.",
            "- If you judge that you already have a complete candidate final result or proof blueprint, call `verify_overall` with `scope=\"final\"` for final review.",
            "- If final review passes, give the final conclusion; otherwise continue repairing the argument.",
        ]
        if current_focus:
            lines.append("- Current focus: %s" % current_focus)
        if current_claim:
            lines.append("- Current claim: %s" % current_claim)
        if blocker:
            lines.append("- Recorded blocker: %s" % blocker)
        if next_action:
            lines.append("- Next concrete action hint: %s" % next_action)
        if str(policy.get("action") or "") == "consolidate":
            lines.extend(
                [
                    "",
                    "Consolidation turn required:",
                    "- Reason: %s" % str(policy.get("reason") or "stability policy"),
                    "- Do not open a new proof branch unless consolidation reveals a precise local next step.",
                    "- Rewrite or tighten `problem.md` only when the active problem itself has changed.",
                    "- Resolve soft blockers by shrinking them into a smaller mathematical check, not by waiting for the user.",
                ]
            )
        if int(stagnant_count or 0) > 0:
            lines.extend(
                [
                    "",
                    "Stability note:",
                    "- The recent workflow signature has repeated %s time(s)." % int(stagnant_count or 0),
                    "- Replan before continuing: identify what has not changed, pick a different local move, and commit the new branch state.",
                ]
            )
            if previous_signature:
                lines.append("- Previous signature: %s" % shorten(previous_signature, 240))
        return "\n".join(lines).strip()

    def _new_state(self, project_slug: str, seed: str = "") -> ResearchWorkflowState:
        now = utc_now()
        return ResearchWorkflowState(
            project_slug=project_slug,
            current_focus="Assess the current research state and choose the best next mathematical move.",
            active_problem=shorten(seed, 1000) if seed else "",
            quality_scores=_default_quality_scores(),
            verification=_default_verification(),
            state_assessment=_default_state_assessment(),
            final_verification_gate=_default_final_verification_gate(),
            next_action="Assess the current research state and choose the best problem-design activity.",
            langgraph_thread_id="moonshine-research-%s" % project_slug,
            created_at=now,
            updated_at=now,
        )

    def load_state(self, project_slug: str, seed: str = "") -> ResearchWorkflowState:
        """Load or initialize the workflow state for a project."""
        try:
            payload = read_json(self._state_path(project_slug), default=None)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and (payload.get("node") or payload.get("focus_activity")):
            return _state_from_payload(payload, project_slug)
        state = self._new_state(project_slug, seed=seed)
        self.save_state(state, mirror_progress=False, checkpoint_reason="initialized")
        return state

    def _runtime_state_payload(
        self,
        state: ResearchWorkflowState,
        *,
        checkpoint_meta: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Build the lightweight runtime state injected into future research turns."""
        spec = ACTIVITY_SPECS[_safe_activity(state.node, state.stage)]
        return {
            "project_slug": state.project_slug,
            "stage": state.stage,
            "focus_activity": state.node,
            "focus_label": spec["label"],
            "status": state.status,
            "active_problem": state.active_problem,
            "active_branch_id": state.active_branch_id,
            "current_focus": state.current_focus or spec["goal"],
            "current_claim": state.current_claim,
            "blocker": state.blocker,
            "next_action": state.next_action or spec["goal"],
            "last_summary": state.last_summary,
            "open_questions": list(state.open_questions[-6:]),
            "failed_paths": list(state.failed_paths[-6:]),
            "pending_verification_items": list(state.pending_verification_items[-6:]),
            "recent_memory_ids": list(state.recent_memory_ids[-8:]),
            "branch_states": list(state.branch_states[-6:]),
            "claim_registry": list(state.claim_registry[-12:]),
            "verified_claim_hashes": _dedupe_strings(
                list(state.verified_claim_hashes[-16:]) + list(self._verified_claim_hash_set(state.project_slug)),
                limit=32,
            ),
            "verified_verification_keys": _dedupe_strings(
                list(self._verified_verification_key_set(state.project_slug)),
                limit=32,
            ),
            "workspace_hashes": dict(state.workspace_hashes or self._workspace_hashes(state.project_slug)),
            "workspace_files": {
                "problem": "projects/%s/workspace/problem.md" % state.project_slug,
                "blueprint": "projects/%s/workspace/blueprint.md" % state.project_slug,
                "verified": "projects/%s/workspace/blueprint_verified.md" % state.project_slug,
            },
            "archive_files": {
                "workflow": self.workflow_state_runtime_path(state.project_slug),
                "runtime_state": self.runtime_state_runtime_path(state.project_slug),
                "research_log": "projects/%s/memory/research_log.jsonl" % state.project_slug,
                "research_log_index": "projects/%s/memory/research_log_index.sqlite" % state.project_slug,
                "verification": self.verification_runtime_path(state.project_slug),
                "checkpoints": "projects/%s/memory/research_checkpoints.jsonl" % state.project_slug,
            },
            "checkpoint_backend": str((checkpoint_meta or {}).get("backend") or state.checkpoint_backend),
            "updated_at": state.updated_at,
        }

    def _append_ledger_entry(self, project_slug: str, payload: Dict[str, object]) -> Dict[str, object]:
        """Legacy ledger writer retained as an in-memory no-op."""
        entry = dict(payload or {})
        if not str(entry.get("id") or "").strip():
            entry["id"] = deterministic_slug(
                "%s %s %s"
                % (
                    str(entry.get("kind") or "ledger"),
                    str(entry.get("title") or ""),
                    str(entry.get("created_at") or utc_now()),
                ),
                str(entry.get("summary") or ""),
                prefix=str(entry.get("kind") or "ledger"),
            )
        return entry

    def _append_workspace_update_event(
        self,
        project_slug: str,
        *,
        kind: str,
        path: str,
        summary: str,
        session_id: str = "",
        source: str = "section_capture",
    ) -> Dict[str, object]:
        """Return a workspace update event without writing legacy project ledger."""
        hashes = self._workspace_hashes(project_slug)
        event = {
            "id": deterministic_slug(
                "workspace_update %s %s" % (str(kind or ""), utc_now()),
                str(path or ""),
                prefix="workspace-update",
            ),
            "kind": "workspace_update",
            "workspace_kind": str(kind or ""),
            "title": "Workspace update: %s" % str(kind or "file"),
            "summary": shorten(summary, 500),
            "status": "captured",
            "updated_files": [path] if path else [],
            "workspace_hashes": hashes,
            "source": source,
            "session_id": session_id,
            "created_at": utc_now(),
        }
        if self.session_store is not None and session_id:
            try:
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "workspace_update",
                        "text": event["summary"],
                        "created_at": event["created_at"],
                        **event,
                    },
                )
            except Exception:
                pass
        return event

    def build_runtime_packet(self, state: ResearchWorkflowState) -> str:
        """Render a high-signal research packet for the main agent prompt."""
        runtime_state = self._runtime_state_payload(state)
        problem_text = self._workspace_packet_excerpt(
            state.project_slug,
            "problem",
            full_token_budget=2200,
            excerpt_char_budget=2200,
        )
        research_log_md_text = read_text(
            self.paths.project_research_log_markdown_file(state.project_slug),
            default="",
        ).strip() or "(empty)"
        research_log_lines = []
        for item in self.research_log.records(state.project_slug)[-4:]:
            research_log_lines.append(
                "- [{record_type}] {title}: {content}".format(
                    record_type=str(item.get("type") or "research_note"),
                    title=str(item.get("title") or "(untitled)"),
                    content=shorten(str(item.get("content") or ""), 260),
                )
            )
        if not research_log_lines:
            research_log_lines.append("- (no research_log records yet)")
        retrieval_query = " ".join(
            part
            for part in [
                str(state.current_claim or ""),
                str(state.current_focus or ""),
                str(state.blocker or ""),
                str(state.next_action or ""),
                str(state.last_summary or ""),
            ]
            if part
        ).strip()
        indexed_lines: List[str] = []
        if retrieval_query:
            try:
                for item in self.research_log.search(query=retrieval_query, project_slug=state.project_slug, limit=3):
                    metadata = dict(item.get("metadata") or {})
                    body = str(item.get("content_inline") or item.get("content") or "").strip()
                    indexed_lines.append(
                        "- [{source}/{kind}] {title}\n"
                        "  Summary: {summary}\n"
                        "  Source refs: {source_refs}\n"
                        "  Precise slice:\n{slice}".format(
                            source=str(item.get("source_type") or metadata.get("source_type") or "research_log"),
                            kind=str(item.get("type") or item.get("artifact_type") or "research_note"),
                            title=str(item.get("title") or "(untitled)"),
                            summary=shorten(body, 240) or "(none)",
                            source_refs=", ".join(str(ref) for ref in list(item.get("source_refs") or [])[:3]) or "(none)",
                            slice=body[:1800].strip() or "(empty)",
                        )
                    )
            except Exception:
                indexed_lines = []
        if not indexed_lines:
            indexed_lines.append("- (no indexed slice selected yet)")
        return (
            "Research runtime packet:\n"
            "- Project research log: `projects/{project_slug}/memory/research_log.jsonl`\n"
            "- Research-log retrieval index: `projects/{project_slug}/memory/research_log_index.sqlite`\n"
            "- Verification digest: `projects/{project_slug}/memory/verification.jsonl`\n"
            "- Current stage/activity: {stage} / {activity}\n"
            "- Active branch: {branch}\n"
            "- Current focus: {focus}\n"
            "- Current claim: {claim}\n"
            "- Blocker: {blocker}\n"
            "- Next action: {next_action}\n"
            "- Last summary: {summary}\n\n"
            "Branch and claim stability:\n"
            "- Verified claim hashes: {verified_hashes}\n"
            "- Verified verification keys: {verification_keys}\n"
            "- Workspace hashes: {workspace_hashes}\n"
            "- Recent branch states: {branches}\n\n"
            "Canonical workspace file:\n"
            "### problem.md\n{problem}\n\n"
            "Human-readable project log:\n"
            "### research_log.md\n{research_log_md}\n\n"
            "Indexed retrieval slices (original source remains in canonical files / research_log / session archives):\n"
            "{indexed}\n\n"
            "Recent research_log records:\n"
            "{research_log}\n"
        ).format(
            project_slug=state.project_slug,
            stage=runtime_state["stage"],
            activity=runtime_state["focus_activity"],
            branch=runtime_state["active_branch_id"] or "(not selected yet)",
            focus=runtime_state["current_focus"] or "(not set)",
            claim=runtime_state["current_claim"] or "(none yet)",
            blocker=runtime_state["blocker"] or "(none recorded)",
            next_action=runtime_state["next_action"] or "(none yet)",
            summary=runtime_state["last_summary"] or "(none yet)",
            verified_hashes=", ".join(runtime_state["verified_claim_hashes"][-8:]) or "(none)",
            verification_keys=", ".join(runtime_state["verified_verification_keys"][-8:]) or "(none)",
            workspace_hashes=json.dumps(runtime_state["workspace_hashes"], ensure_ascii=False),
            branches=json.dumps(runtime_state["branch_states"][-4:], ensure_ascii=False) if runtime_state["branch_states"] else "(none)",
            problem=problem_text,
            research_log_md=research_log_md_text,
            indexed="\n".join(indexed_lines),
            research_log="\n".join(research_log_lines),
        )

    def save_state(
        self,
        state: ResearchWorkflowState,
        *,
        mirror_progress: bool = False,
        checkpoint_reason: str = "state_saved",
    ) -> Dict[str, object]:
        """Persist lightweight workflow state and checkpoint the graph snapshot."""
        state.updated_at = utc_now()
        state.workspace_hashes = self._workspace_hashes(state.project_slug)
        state.verified_claim_hashes = _dedupe_strings(
            list(state.verified_claim_hashes or []) + list(self._verified_claim_hash_set(state.project_slug)),
            limit=64,
        )
        snapshot = state.to_dict()
        checkpoint_meta = self.checkpoints.save(project_slug=state.project_slug, snapshot=snapshot)
        state.checkpoint_backend = str(checkpoint_meta.get("backend") or "file_jsonl")
        snapshot = state.to_dict()
        atomic_write(self._state_path(state.project_slug), json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
        atomic_write(
            self._runtime_state_path(state.project_slug),
            json.dumps(
                self._runtime_state_payload(state, checkpoint_meta=checkpoint_meta),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )
        if mirror_progress and self.memory_manager is not None:
            self._mirror_progress_entry(state, checkpoint_meta=checkpoint_meta)
        try:
            self.research_index.rebuild_project(state.project_slug)
        except Exception:
            pass
        return {"reason": checkpoint_reason, **checkpoint_meta}

    def _mirror_progress_entry(self, state: ResearchWorkflowState, *, checkpoint_meta: Optional[Dict[str, object]] = None) -> None:
        """Legacy dynamic-memory progress mirror is disabled in research mode."""
        return

    def ensure_started(self, *, project_slug: str, user_message: str = "", session_id: str = "") -> ResearchWorkflowState:
        """Ensure a workflow exists and record a lightweight start trace."""
        state = self.load_state(project_slug, seed=user_message)
        if self.session_store is not None and state.iteration_count == 0:
            self.session_store.append_turn_event(
                session_id,
                {
                    "type": "research_workflow_started",
                    "text": "Adaptive research workflow is active.",
                    "project_slug": project_slug,
                    "activity": state.node,
                    "node": state.node,
                    "stage": state.stage,
                    "checkpoint_backend": state.checkpoint_backend,
                    "created_at": utc_now(),
                },
            )
        return state

    def build_prompt(self, state: ResearchWorkflowState) -> str:
        """Render the current research snapshot and stage guidance for the main agent."""
        spec = ACTIVITY_SPECS[_safe_activity(state.node, state.stage)]
        stage_activities = [
            "%s: %s" % (ACTIVITY_SPECS[item]["label"], ACTIVITY_SPECS[item]["goal"])
            for item in ACTIVITIES_BY_STAGE[state.stage]
        ]
        skill_list = ", ".join("$%s" % item for item in list(spec.get("skills") or []))
        tool_list = ", ".join("`%s`" % item for item in list(spec.get("tools") or []))
        recent_artifacts = [
            "- [%s] %s: %s"
            % (
                str(item.get("artifact_type", "")),
                str(item.get("title", "")),
                str(item.get("summary", "")),
            )
            for item in list(state.recent_artifacts or [])[-6:]
        ]
        problem_review = dict(state.problem_review or _default_problem_review())
        transition_status = dict(state.transition_status or _default_transition_status())
        problem_excerpt = self._workspace_file_excerpt(state.project_slug, "problem", limit=900)
        research_log_excerpt = shorten(
            read_text(self.paths.project_research_log_markdown_file(state.project_slug), default="").strip(),
            3600,
        ) or "(empty)"
        navigation_brief = self._navigation_memory_brief(state.project_slug, state.stage)
        return (
            "Advance the project from the following research context:\n"
            "- Status: {status}\n"
            "- Stage: {stage}\n"
            "- Focus activity: {label} (`{activity}`)\n"
            "- Iteration: {iteration}\n"
            "- Active problem: {problem}\n"
            "- Latest problem review: {problem_review}\n"
            "- Stage transition status: {transition_status}\n"
            "- Completed activities: {completed}\n"
            "- Open questions: {open_questions}\n"
            "- Failed paths: {failed_paths}\n"
            "- Last assessment: {assessment}\n"
            "- Last selected skills/tools: {selected}\n"
            "- Pending verification targets: {pending_verification}\n"
            "- Final verification gate: {final_gate}\n"
            "- Last summary: {summary}\n\n"
            "Current project files:\n"
            "- `projects/{project_slug}/workspace/problem.md`:\n{problem_excerpt}\n"
            "- `projects/{project_slug}/memory/research_log.md`:\n{research_log_excerpt}\n\n"
            "Navigation memory index:\n"
            "{navigation_brief}\n\n"
            "Stage Activity Palette:\n"
            "{stage_activities}\n\n"
            "Recent Research-Log Records:\n"
            "{recent_artifacts}\n\n"
            "Current Focus Contract:\n"
            "- Goal: {goal}\n"
            "- Suggested skills: {skills}\n"
            "- Suggested tools: {tools}\n"
            "- Good outputs: {good_outputs}\n\n"
            "Working reminders:\n"
            "- Saved evidence outranks optimistic prose.\n"
            "- If the research problem changes, state it clearly and keep `workspace/problem.md` aligned.\n"
            "- Use `memory/research_log.jsonl`, `memory/research_log.md`, `memory/by_type/*.md`, and `memory/research_log_index.sqlite` when prior project work matters.\n"
            "- State durable mathematical progress clearly; the archival pass will save it into `memory/research_log.jsonl` for later retrieval.\n"
            "- Use verification tools as evidence-backed support.\n"
            "- Work as a professional mathematical researcher and keep the reply centered on mathematical claims, constructions, checks, experiments, verifier evidence, and concrete file updates."
        ).format(
            status=state.status,
            stage=state.stage,
            label=spec["label"],
            activity=state.node,
            iteration=state.iteration_count,
            problem=state.active_problem or "(not selected yet)",
            problem_review=str(problem_review.get("summary") or problem_review.get("review_status") or "(none yet)"),
            transition_status=str(transition_status.get("reason") or "(no stage transition recorded)"),
            completed=", ".join(state.completed_nodes) or "(none yet)",
            open_questions="; ".join(state.open_questions[-5:]) or "(none yet)",
            failed_paths="; ".join(state.failed_paths[-5:]) or "(none yet)",
            assessment=str((state.state_assessment or {}).get("rationale") or (state.state_assessment or {}).get("current_focus") or "(none yet)"),
            selected=", ".join((state.selected_skills + state.selected_tools)[-8:]) or "(none yet)",
            pending_verification="; ".join(state.pending_verification_items[-5:]) or "(none)",
            final_gate=str((state.final_verification_gate or {}).get("reason") or "(not ready)"),
            summary=shorten(state.last_summary, 400) or "(none yet)",
            project_slug=state.project_slug,
            problem_excerpt=problem_excerpt,
            research_log_excerpt=research_log_excerpt,
            navigation_brief=navigation_brief,
            stage_activities="\n".join("- %s" % item for item in stage_activities),
            recent_artifacts="\n".join(recent_artifacts) or "- (none yet)",
            goal=spec["goal"],
            skills=skill_list or "(none)",
            tools=tool_list or "(none)",
            good_outputs=", ".join(list(spec.get("good_outputs") or [])),
        )

    def _can_use_structured_llm(self) -> bool:
        return self.provider is not None and not isinstance(self.provider, OfflineProvider) and hasattr(self.provider, "generate_structured")

    def _structured_update(
        self,
        *,
        state: ResearchWorkflowState,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> Optional[Dict[str, object]]:
        if not self._can_use_structured_llm():
            return None
        spec = ACTIVITY_SPECS[_safe_activity(state.node, state.stage)]
        evidence_package = self._controller_evidence_package(state=state, session_id=session_id)
        prompt = (
            "Update Moonshine's adaptive two-stage research workflow after one assistant turn.\n"
            "Use the schema strictly. Do not invent progress that is not supported by the execution evidence.\n"
            "The workflow is not a rigid node chain. Select the next activity that best fits the evidence.\n\n"
            "State-update requirements:\n"
            "- First assess the state, then choose skills/tools, then choose the next activity.\n"
            "- Use the loaded AGENTS-style instructions as the source of truth for skill trigger conditions.\n"
            "- In control_selection.trigger_rules_used, cite the concrete trigger rules that justified the selected skills/tools.\n"
            "- In instruction_conflicts, report conflicts between project AGENTS.md, builtin trigger preferences, model output, and hard invariants.\n"
            "- Resolve conflicts by this precedence: hard invariants > project AGENTS.md > builtin trigger preferences > model judgment.\n"
            "- Ground every state judgment in the evidence package, not in assistant prose alone.\n"
            "- If assistant prose conflicts with tool results, verifier payloads, research-log records, or workspace files, prefer the structured evidence.\n"
            "- Record failed attempts explicitly so the agent does not repeat them.\n"
            "- If the turn produced an intermediate lemma/claim, require pessimistic verification before it can be formal knowledge.\n"
            "- If a conclusion is not verified, keep it as a candidate progress artifact instead of formal knowledge.\n"
            "- Only set final_verification_gate.ready_for_final_verification=true when a complete formal blueprint or answer exists.\n"
            "- Only treat a proof as verified if the evidence package contains a real verification verdict; never infer verification from optimistic prose.\n"
            "- If verification failed repeatedly, prefer strengthening, problem_decomposition, solver_branching, or returning to problem_design instead of another local patch.\n\n"
            "Current state:\n%s\n\n"
            "Current focus activity:\n%s\n\n"
            "Activities in current stage:\n%s\n\n"
            "Evidence Package:\n%s\n\n"
            "User message:\n%s\n\n"
            "Assistant response:\n%s"
        ) % (
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            json.dumps(spec, ensure_ascii=False, indent=2),
            json.dumps(ACTIVITIES_BY_STAGE[state.stage], ensure_ascii=False),
            evidence_package,
            user_message,
            assistant_message,
        )
        payload = self.provider.generate_structured(
            system_prompt=(
                "You are a strict but adaptive research workflow reducer. "
                "You preserve the two-stage protocol, but you do not force a fixed sequence inside a stage. "
                "You ground state changes in tool results, verifier payloads, persisted research artifacts, and workspace files rather than free-form prose. "
                "You never mark a proof verified unless the evidence package contains a real verification verdict. "
                "You keep unverified claims out of formal knowledge and route them to project-memory candidates."
            ),
            messages=[{"role": "user", "content": prompt}],
            response_schema=RESEARCH_WORKFLOW_UPDATE_SCHEMA,
            schema_name="research_workflow_update",
        )
        validate_json_schema(payload, RESEARCH_WORKFLOW_UPDATE_SCHEMA)
        return dict(payload)

    def _heuristic_update(self, *, state: ResearchWorkflowState, assistant_message: str) -> Dict[str, object]:
        text = str(assistant_message or "")
        lowered = text.lower()
        recommended = state.node
        stage_decision = "stay_in_stage"
        verification = dict(state.verification or _default_verification())

        if state.stage == "problem_design":
            if "quality" in lowered or "score" in lowered:
                recommended = "quality_evaluation"
            elif "candidate problem" in lowered or "conjecture" in lowered:
                recommended = "problem_generation"
            elif "refine" in lowered or "too broad" in lowered:
                recommended = "problem_refinement"
            elif any(marker in lowered for marker in ["selected problem", "ready to solve", "move to solving"]):
                stage_decision = "advance_to_problem_solving"
                recommended = "problem_decomposition"
        else:
            if "failed path" in lowered:
                recommended = "solver_branching"
            if "lemma" in lowered or "proposition" in lowered:
                recommended = "lemma_extraction"
            if "verification" in lowered or "gap" in lowered:
                recommended = "pessimistic_verification"
            if any(marker in lowered for marker in ["verified", "passes verification", "no serious gap"]):
                verification = {"verdict": "verified", "critical_errors": [], "rationale": "Heuristic verification marker found."}
                recommended = "persistence"
            elif state.node == "pessimistic_verification":
                verification = {
                    "verdict": "needs_correction",
                    "critical_errors": ["No explicit verification pass was detected."],
                    "rationale": "Pessimistic fallback requires correction unless a pass marker is present.",
                }
                recommended = "correction"

        return {
            "state_assessment": {
                "current_focus": state.next_action or ACTIVITY_SPECS[_safe_activity(state.node, state.stage)]["goal"],
                "search_sufficiency": "insufficient" if state.iteration_count <= 1 and state.stage == "problem_design" else "adequate",
                "reasoning_state": "stuck" if "stuck" in lowered or "failed" in lowered else "making_progress",
                "memory_need": "query_project" if state.iteration_count <= 1 else "write_project",
                "risk_level": "high" if "gap" in lowered else "medium",
                "rationale": "Heuristic assessment inferred from the assistant checkpoint.",
            },
            "control_selection": {
                "selected_skills": list(ACTIVITY_SPECS[_safe_activity(recommended, stage_decision if stage_decision in ACTIVITIES_BY_STAGE else None)].get("skills") or [])[:4]
                if recommended in ACTIVITY_SPECS
                else [],
                "selected_tools": list(ACTIVITY_SPECS[_safe_activity(recommended, stage_decision if stage_decision in ACTIVITIES_BY_STAGE else None)].get("tools") or [])[:4]
                if recommended in ACTIVITY_SPECS
                else [],
                "trigger_rules_used": [
                    "Use the activity's suggested skills/tools when the current state points to that activity.",
                ],
                "selection_rationale": "Heuristic controller selected the next activity from turn content.",
            },
            "activity_status": "checkpointed" if text.strip() else "in_progress",
            "recommended_next_activity": recommended,
            "stage_decision": stage_decision,
            "active_problem": state.active_problem,
            "candidate_problems": list(state.candidate_problems),
            "quality_scores": dict(state.quality_scores or _default_quality_scores()),
            "verification": verification,
            "open_questions": list(state.open_questions),
            "failed_paths": list(state.failed_paths),
            "research_artifacts": _empty_research_artifacts(),
            "instruction_conflicts": [],
            "branch_updates": list(state.branch_states),
            "conclusions_to_store": [],
            "intermediate_verification": _default_intermediate_verification(),
            "final_verification_gate": _default_final_verification_gate(),
            "memory_updates": [],
            "summary": shorten(text, 1200) or "No assistant progress was available.",
            "next_action": "",
            "controller_rationale": "Heuristic update kept the workflow adaptive and conservative.",
            "confidence": 0.35,
        }

    def _resolve_next_stage_and_activity(self, state: ResearchWorkflowState, update: Dict[str, object]) -> Tuple[str, str, str]:
        stage_decision = str(update.get("stage_decision") or "stay_in_stage")
        recommended = str(update.get("recommended_next_activity") or "stay")
        next_stage = state.stage

        if stage_decision == "advance_to_problem_solving":
            next_stage = "problem_solving"
        elif stage_decision == "return_to_problem_design":
            next_stage = "problem_design"
        elif stage_decision == "complete_project":
            next_stage = "problem_solving"

        if recommended == "stay":
            next_activity = state.node if _stage_for_activity(state.node) == next_stage else _default_activity_for_stage(next_stage)
        elif recommended in ACTIVITY_SPECS:
            next_activity = recommended
            next_stage = _stage_for_activity(next_activity)
        else:
            next_activity = _default_activity_for_stage(next_stage)

        if stage_decision == "advance_to_problem_solving" and _stage_for_activity(next_activity) != "problem_solving":
            next_activity = "problem_decomposition"
        if stage_decision == "return_to_problem_design" and _stage_for_activity(next_activity) != "problem_design":
            next_activity = "problem_refinement"
        if stage_decision == "complete_project":
            next_activity = "persistence"

        if stage_decision == "advance_to_problem_solving":
            candidate_state = _state_from_payload(state.to_dict(), state.project_slug)
            candidate_problem = str(update.get("active_problem") or candidate_state.active_problem).strip()
            if candidate_problem:
                candidate_state.active_problem = candidate_problem
            if update.get("quality_scores"):
                candidate_state.quality_scores = dict(update.get("quality_scores") or candidate_state.quality_scores or _default_quality_scores())
            approved, reason = self.can_enter_problem_solving(candidate_state)
            state.transition_status = {
                "last_attempted_stage": "problem_solving",
                "approved": approved,
                "reason": reason,
                "updated_at": utc_now(),
            }
            if not approved:
                next_stage = "problem_design"
                review = dict(candidate_state.problem_review or _default_problem_review())
                review_status = str(review.get("review_status") or "not_reviewed")
                next_activity = "quality_evaluation" if review_status != "passed" else "problem_refinement"
                update["next_action"] = (
                    "The problem has not yet passed a dedicated quality-assessor review. "
                    "Call `load_skill_definition` for `quality-assessor`, then call `assess_problem_quality` once, "
                    "and only then proceed into problem-solving work."
                )

        verification = dict(update.get("verification") or {})
        if state.stage == "problem_solving" and state.node == "pessimistic_verification":
            if verification.get("verdict") != "verified" and next_activity == "persistence":
                next_activity = "correction"
                next_stage = "problem_solving"

        final_gate = dict(update.get("final_verification_gate") or {})
        if stage_decision == "complete_project":
            if not final_gate.get("ready_for_final_verification") or verification.get("verdict") != "verified":
                next_stage = "problem_solving"
                next_activity = "pessimistic_verification" if final_gate.get("has_complete_answer") else "proof_integration"

        if (
            next_activity == "correction"
            and int(state.correction_attempts or 0) >= 3
            and verification.get("verdict") in {"needs_correction", "incorrect"}
        ):
            next_activity = "strengthening"
            next_stage = "problem_solving"

        return next_stage, next_activity, stage_decision

    def _append_research_channel(
        self,
        *,
        project_slug: str,
        channel: str,
        text: str,
        session_id: str,
        activity: str,
        metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        """Old project research-log channel writer retained as a no-op.

        Research mode stores project memory only through research_log.jsonl and
        research_log_index.sqlite. Old channels/*.jsonl files are not written.
        """
        return

    def _persist_research_artifacts(
        self,
        *,
        update: Dict[str, object],
        state: ResearchWorkflowState,
        session_id: str,
    ) -> None:
        """Legacy structured-artifact channel persistence is disabled."""
        return

    def _latest_main_round_ref(self, session_id: str) -> str:
        """Return the latest main provider-round archive path for provenance."""
        if self.session_store is None or not session_id:
            return ""
        try:
            rounds = self.session_store.get_provider_rounds(session_id)
        except Exception:
            return ""
        for item in reversed(list(rounds or [])):
            if str(item.get("phase") or "") != "main":
                continue
            archive_path = str(item.get("archive_path") or "").strip()
            if archive_path:
                return archive_path
            round_id = str(item.get("id") or "").strip()
            if round_id:
                return self.paths.session_provider_rounds_file(session_id).relative_to(self.paths.home).as_posix() + "#" + round_id
        return ""

    def _research_log_archive_context(self, project_slug: str, records: List[Dict[str, object]]) -> str:
        """Return full or compressed research-log context for the archival pass."""
        full_text = render_research_log_for_archive(records)
        threshold = self._archive_context_threshold()
        model_name = str(getattr(getattr(self.config, "provider", None), "model", "") or "")
        if estimate_token_count(full_text, model_name=model_name) <= threshold:
            return full_text
        record_ids = [str(item.get("id") or "") for item in records]
        summaries = self.research_log._summary_rows(project_slug)
        for item in reversed(summaries):
            covered_ids = [str(value) for value in list(item.get("covered_record_ids") or [])]
            if not covered_ids or record_ids[: len(covered_ids)] != covered_ids:
                continue
            tail_records = records[len(covered_ids) :]
            candidate = (
                "<compressed-research-log>\n"
                "The project research log prefix is compressed below. If exact original records are needed, read "
                "`projects/%s/memory/research_log.jsonl`.\n\n%s\n</compressed-research-log>\n\n"
                "New uncompressed records after that summary:\n%s"
            ) % (
                project_slug,
                str(item.get("summary") or ""),
                render_research_log_for_archive(tail_records) or "(none)",
            )
            if estimate_token_count(candidate, model_name=model_name) <= threshold:
                return candidate
        summary = self._compress_research_log_records(project_slug, records)
        return (
            "<compressed-research-log>\n"
            "The project research log is compressed below. If exact original records are needed, read "
            "`projects/%s/memory/research_log.jsonl`.\n\n%s\n</compressed-research-log>"
        ) % (project_slug, summary)

    def _compress_research_log_records(self, project_slug: str, records: List[Dict[str, object]]) -> str:
        """Compress the full original research log from records, not from older summaries."""
        if not records:
            return ""
        chunk_count = 60
        chunk_size = max(1, (len(records) + chunk_count - 1) // chunk_count)
        chunk_summaries: List[str] = []
        for index in range(0, len(records), chunk_size):
            chunk = records[index : index + chunk_size]
            text = render_research_log_for_archive(chunk)
            summary = self._summarize_research_log_text(text, index // chunk_size + 1)
            chunk_summaries.append(summary)
        final_summary = "\n\n".join(chunk_summaries)
        self.research_log.append_summary(
            project_slug,
            {
                "created_at": utc_now(),
                "covered_record_ids": [str(item.get("id") or "") for item in records],
                "summary": final_summary,
                "source_path": self._research_log_path(project_slug).relative_to(self.paths.home).as_posix(),
            },
        )
        return final_summary

    def _summarize_research_log_text(self, text: str, chunk_index: int) -> str:
        """Summarize one research-log chunk as a research progress report."""
        if self.provider is None or isinstance(self.provider, OfflineProvider) or not hasattr(self.provider, "generate"):
            return shorten(text, 4000)
        prompt = (
            "Compress this project research_log chunk as a research progress report. "
            "Preserve concrete mathematical statements, verified conclusions, counterexamples, failed paths, "
            "verification outcomes, and next useful research context. Do not invent new content.\n\n"
            "Chunk %s:\n%s"
        ) % (chunk_index, text)
        try:
            response = self.provider.generate(
                system_prompt="You compress research memory while preserving scientific details.",
                messages=[{"role": "user", "content": prompt}],
                tool_schemas=[],
            )
            return str(getattr(response, "content", "") or "").strip() or shorten(text, 4000)
        except Exception:
            return shorten(text, 4000)

    def _render_turn_context_for_archive(self, turn_context: Sequence[Dict[str, object]]) -> str:
        """Render current-turn context in a readable execution order."""
        lines = []
        for index, raw in enumerate(list(turn_context or []), start=1):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            kind = str(item.get("kind") or "event")
            if kind == "user_input":
                lines.append("## %s. User input\n\n%s" % (index, str(item.get("content") or "").strip()))
            elif kind == "assistant_output":
                source = str(item.get("source") or "").strip()
                label = "Assistant output" + (" (%s)" % source if source else "")
                lines.append("## %s. %s\n\n%s" % (index, label, str(item.get("content") or "").strip()))
            elif kind == "assistant_tool_calls":
                tool_calls = list(item.get("tool_calls") or [])
                call_lines = []
                for call_index, call in enumerate(tool_calls, start=1):
                    if not isinstance(call, dict):
                        continue
                    call_lines.append(
                        "### Tool call %s: %s\n\nArguments:\n```json\n%s\n```\nStatus: %s%s"
                        % (
                            call_index,
                            str(call.get("name") or ""),
                            json.dumps(dict(call.get("arguments") or {}), ensure_ascii=False, indent=2),
                            str(call.get("status") or ""),
                            ("\nError: %s" % str(call.get("error"))) if str(call.get("error") or "") else "",
                        )
                    )
                prefix = str(item.get("content") or "").strip()
                body = (prefix + "\n\n" if prefix else "") + ("\n\n".join(call_lines) if call_lines else "(no tool calls)")
                lines.append("## %s. Assistant tool calls\n\n%s" % (index, body.strip()))
            elif kind == "tool_result":
                lines.append(
                    "## %s. Tool result: %s\n\nCall id: `%s`\nTool round: %s\n\nArguments:\n```json\n%s\n```\n\nOutput:\n```json\n%s\n```\n%s"
                    % (
                        index,
                        str(item.get("tool") or ""),
                        str(item.get("call_id") or ""),
                        str(item.get("tool_round") or ""),
                        json.dumps(dict(item.get("arguments") or {}), ensure_ascii=False, indent=2),
                        json.dumps(item.get("output"), ensure_ascii=False, indent=2),
                        ("\nError: %s" % str(item.get("error"))) if item.get("error") else "",
                    )
                )
            else:
                lines.append("## %s. %s\n\n```json\n%s\n```" % (index, kind, json.dumps(item, ensure_ascii=False, indent=2)))
        return "\n\n".join(part.strip() for part in lines if part.strip())

    def _archive_context_threshold(self) -> int:
        """Return the token threshold for archival context material."""
        return max(
            1000,
            int(getattr(getattr(self.config, "context", None), "compression_threshold_tokens", 200000) or 200000),
        )

    def _archive_research_turn(
        self,
        *,
        project_slug: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        turn_context: Optional[List[Dict[str, object]]] = None,
    ) -> Dict[str, object]:
        """Archive this turn into the simplified project research log."""
        if self.provider is None or isinstance(self.provider, OfflineProvider) or not hasattr(self.provider, "generate_structured"):
            return {"archived": 0, "skipped": "structured_provider_unavailable"}
        existing_records = self.research_log.records(project_slug)
        existing_log_text = self._research_log_archive_context(project_slug, existing_records)
        round_ref = self._latest_main_round_ref(session_id)
        source_refs = []
        if round_ref:
            source_refs.append(round_ref)
        if session_id:
            source_refs.extend(
                [
                    self.paths.session_messages_file(session_id).relative_to(self.paths.home).as_posix(),
                    self.paths.session_tool_events_file(session_id).relative_to(self.paths.home).as_posix(),
                ]
            )
        full_turn_context = list(turn_context or [])
        if not full_turn_context:
            full_turn_context = [
                {"kind": "user_input", "content": user_message},
                {"kind": "assistant_output", "content": assistant_message},
            ]
        current_turn_context = self._render_turn_context_for_archive(full_turn_context)
        type_contract = (
            "Allowed research-log types and their exclusive meanings:\n"
            "- problem: The research object itself: researched problems, candidate problems, problem revisions, or final problem statements.\n"
            "- verified_conclusion: A reusable mathematical result that has passed verification, including verified intermediate lemmas/conclusions. Its center is the claim itself.\n"
            "- verification: A review/checking report about a claim, problem, proof, computation, or final result. Its center is the checking process, verdict, objections, scores, gaps, or repair targets.\n"
            "- final_result: A final theorem, final answer, or final report-level result of the project.\n"
            "- counterexample: A construction, example, or argument that refutes a claim.\n"
            "- failed_path: A route, method, proof strategy, estimate, or branch that failed or became unusable; it may coexist with a counterexample record from the same turn.\n"
            "- research_note: Any other useful research progress, calculation, plan, attempt, observation, or local derivation.\n\n"
            "Classification rules:\n"
            "- Each record must use exactly one type from the list above.\n"
            "- If the current turn contains multiple kinds of material, split it into multiple records.\n"
            "- Prefer the most specific type: verified_conclusion over research_note for verified claims, counterexample over research_note for refutations, and failed_path over research_note for failed methods.\n"
            "- Do not create a verified_conclusion unless the current turn or tool results show that the conclusion passed verification.\n"
            "- Use verification for quality-assessor reviews, verify_* tool outputs, failed checks, gap reports, and repair guidance.\n"
            "- Use verified_conclusion only when there is a standalone mathematical statement that future turns can cite as true. Do not use it merely because a verification call occurred.\n"
            "- When a verification call passes a reusable claim, you may create both records: verification for the review report and verified_conclusion for the reusable mathematical statement. If this would duplicate content, prefer verified_conclusion plus a short verification note inside it.\n"
            "\n"
            "Content requirements by type:\n"
            "- problem: State the exact research problem or revision, including objects, hypotheses, target conclusion/question, and any explicit scope restrictions. Do not mix in proof attempts unless they require separate records.\n"
            "- verified_conclusion: Include Statement, assumptions, proof sketch, verification evidence, and limitations. It should read like a citable lemma/conclusion, not like a review report.\n"
            "- verification: Include Checked item, tool/reviewer/scope, verdict or score, objections/gaps, and repair targets. It should read like an audit report, not like a theorem statement.\n"
            "- final_result: Include the final theorem/result, hypotheses, conclusion, proof outline, final verification status, and remaining limitations or open ends.\n"
            "- counterexample: Include the claim being refuted, the construction or parameters, the calculation/check showing failure, and any caveats about scope.\n"
            "- failed_path: Include the route or method attempted, its goal, the exact reason it failed or became too weak, evidence from the turn, and the lesson for future work.\n"
            "- research_note: Include the concrete calculation, observation, plan, partial derivation, or local progress. Avoid vague diary text; include the next useful action only if it appears in the turn.\n"
        )
        prompt = (
            "Archive the completed research turn into simple project memory.\n"
            "Extract only material that appears in the current turn full context below or the existing research log. "
            "Do not invent new mathematics. Split mixed material into multiple records.\n\n"
            "%s\n"
            "Write each content field as a clear small research record about the current turn. "
            "No output length limit is imposed, but keep records focused.\n\n"
            "Existing project research_log.jsonl contents:\n%s\n\n"
            "Current turn full context, in execution order. This includes user input, assistant tool calls, skill/tool results, and assistant outputs:\n%s"
        ) % (
            type_contract,
            existing_log_text or "(empty)",
            current_turn_context,
        )
        payload = self.provider.generate_structured(
            system_prompt=(
                "You are a strict research archivist for Moonshine research mode. "
                "You extract per-turn research records without adding new claims. "
                "You must classify every record with exactly one of these types: "
                "problem, verified_conclusion, verification, final_result, counterexample, failed_path, research_note. "
                "Definitions: problem means the research object or its revisions; "
                "verified_conclusion means a reusable verified mathematical result centered on the claim itself; "
                "verification means a review/checking report centered on verdicts, scores, objections, gaps, or repair targets; "
                "final_result means a final theorem, answer, or report-level result; "
                "counterexample means a construction or argument refuting a claim; "
                "failed_path means a failed method, route, proof strategy, estimate, or branch and may coexist with counterexample; "
                "research_note means all other useful research progress, calculations, plans, attempts, observations, or local derivations. "
                "Split mixed material into multiple records and do not create verified_conclusion unless verification passed. "
                "Follow the per-type content requirements in the user message."
            ),
            messages=[{"role": "user", "content": prompt}],
            response_schema=RESEARCH_ARCHIVE_SCHEMA,
            schema_name="research_turn_archive",
        )
        validate_json_schema(payload, RESEARCH_ARCHIVE_SCHEMA)
        if self.session_store is not None and session_id:
            try:
                self.session_store.append_provider_round(
                    session_id,
                    {
                        "created_at": utc_now(),
                        "phase": "archive",
                        "title": "Research Log Archive",
                        "model_round": 0,
                        "system_prompt": "You are a strict research archivist for Moonshine research mode.",
                        "messages": [{"role": "user", "content": prompt}],
                        "tool_schema_names": [],
                        "response": {"content": json.dumps(payload, ensure_ascii=False), "tool_calls": []},
                    },
                )
            except Exception:
                pass
        raw_records = []
        for item in list(payload.get("records") or []):
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item["project_slug"] = project_slug
            item["session_id"] = session_id
            item["round_id"] = round_ref
            item["source_refs"] = list(source_refs)
            raw_records.append(item)
        created = self.research_log.append_records(project_slug, raw_records)
        problem_records = [item for item in created if str(item.get("type") or "") == "problem"]
        if problem_records:
            latest_problem = str(problem_records[-1].get("content") or "").strip()
            if latest_problem:
                state = ResearchWorkflowState(project_slug=project_slug)
                self._write_problem_draft(state, explicit_body=latest_problem)
        return {
            "archived": len(created),
            "records": [dict(item) for item in created],
            "research_log": self._research_log_path(project_slug).relative_to(self.paths.home).as_posix(),
        }

    def _write_candidate_conclusion(
        self,
        *,
        conclusion: Dict[str, object],
        update: Dict[str, object],
        state: ResearchWorkflowState,
        session_id: str,
    ) -> str:
        """Persist an unverified conclusion as a candidate project lemma."""
        title = "Candidate: %s" % str(conclusion.get("title", "Intermediate conclusion"))
        body = (
            "Unverified intermediate conclusion candidate.\n\n"
            "Statement: {statement}\n\n"
            "Proof sketch/evidence: {proof_sketch}\n\n"
            "Status: {status}\n"
            "Verification: {verification}\n"
        ).format(
            statement=str(conclusion.get("statement", "")),
            proof_sketch=str(conclusion.get("proof_sketch", "")),
            status=str(conclusion.get("status", "draft")),
            verification=json.dumps(update.get("intermediate_verification") or {}, ensure_ascii=False),
        )
        entry = self.memory_manager.dynamic_store.make_entry(
            alias="project-lemmas",
            slug=deterministic_slug(title, str(conclusion.get("statement", "")), prefix="candidate-lemma"),
            title=title,
            summary=shorten(str(conclusion.get("statement", "")), 160),
            body=body,
            source="research-workflow-candidate",
            project_slug=state.project_slug,
            tags=list(conclusion.get("tags") or []) + ["candidate", "unverified"],
            source_session_id=session_id,
            source_message_role="assistant",
            source_excerpt=str(update.get("summary") or ""),
        )
        self.memory_manager.dynamic_store.write_entry(entry)
        return str(self.paths.home / entry.relative_path)

    def _write_structured_artifacts(
        self,
        *,
        update: Dict[str, object],
        state: ResearchWorkflowState,
        session_id: str,
    ) -> List[str]:
        updated_files: List[str] = []
        if self.memory_manager is None:
            return updated_files

        self._persist_research_artifacts(update=update, state=state, session_id=session_id)

        for item in list(update.get("memory_updates") or []):
            entry = self.memory_manager.dynamic_store.make_entry(
                alias=str(item["alias"]),
                slug=deterministic_slug(str(item["title"]), str(item["summary"]), prefix=str(item["alias"])),
                title=str(item["title"]),
                summary=str(item["summary"]),
                body=str(item["body"]),
                source="research-workflow",
                project_slug=state.project_slug,
                tags=list(item.get("tags") or []),
                source_session_id=session_id,
                source_message_role="assistant",
                source_excerpt=str(update.get("summary") or ""),
            )
            self.memory_manager.dynamic_store.write_entry(entry)
            updated_files.append(str(self.paths.home / entry.relative_path))

        intermediate = dict(update.get("intermediate_verification") or {})
        turn_verification = dict(update.get("verification") or {})
        for conclusion in list(update.get("conclusions_to_store") or []):
            verified_for_knowledge = (
                str(conclusion.get("status", "")) == "verified"
                and (
                    intermediate.get("verdict") == "passed"
                    or turn_verification.get("verdict") == "verified"
                )
            )
            if not verified_for_knowledge:
                updated_files.append(
                    self._write_candidate_conclusion(
                        conclusion=conclusion,
                        update=update,
                        state=state,
                        session_id=session_id,
                    )
                )
                self._append_research_channel(
                    project_slug=state.project_slug,
                    channel="immediate_conclusions",
                    text=str(conclusion.get("statement", "")),
                    session_id=session_id,
                    activity=state.node,
                    metadata={"stored_as": "candidate_project_lemma", "status": conclusion.get("status", "draft")},
                )
                continue
            conclusion_id = self.memory_manager.knowledge_store.add_conclusion(
                title=str(conclusion["title"]),
                statement=str(conclusion["statement"]),
                proof_sketch=str(conclusion.get("proof_sketch", "")),
                status=str(conclusion.get("status", "partial")),
                project_slug=state.project_slug,
                tags=list(conclusion.get("tags") or []) + ["research-workflow"],
                source_type="research-workflow",
                source_ref=session_id,
            )
            updated_files.append(str(self.memory_manager.knowledge_store.entry_path(conclusion_id)))

        if updated_files:
            self.memory_manager.dynamic_store.rebuild_index()
        return sorted(set(updated_files))

    def apply_update(
        self,
        *,
        state: ResearchWorkflowState,
        update: Dict[str, object],
        session_id: str = "",
    ) -> Dict[str, object]:
        """Apply one validated adaptive update and persist it."""
        validate_json_schema(update, RESEARCH_WORKFLOW_UPDATE_SCHEMA)
        previous_activity = state.node
        previous_stage = state.stage
        state.iteration_count += 1
        if state.stage == "problem_design":
            state.design_iteration_count += 1
        else:
            state.solving_iteration_count += 1

        if update.get("active_problem"):
            state.active_problem = str(update.get("active_problem") or "").strip()
        if update.get("candidate_problems"):
            state.candidate_problems = [dict(item) for item in list(update.get("candidate_problems") or [])]
        state.quality_scores = dict(update.get("quality_scores") or state.quality_scores or _default_quality_scores())
        state.verification = dict(update.get("verification") or state.verification or _default_verification())
        state.state_assessment = dict(update.get("state_assessment") or state.state_assessment or _default_state_assessment())
        control_selection = dict(update.get("control_selection") or _default_control_selection())
        state.selected_skills = [str(item) for item in list(control_selection.get("selected_skills") or [])]
        state.selected_tools = [str(item) for item in list(control_selection.get("selected_tools") or [])]
        trigger_rules_used = [str(item) for item in list(control_selection.get("trigger_rules_used") or [])]
        instruction_conflicts = [dict(item) for item in list(update.get("instruction_conflicts") or []) if isinstance(item, dict)]
        intermediate_verification = dict(update.get("intermediate_verification") or _default_intermediate_verification())
        state.pending_verification_items = _dedupe_strings(list(intermediate_verification.get("targets") or []))
        state.final_verification_gate = dict(update.get("final_verification_gate") or state.final_verification_gate or _default_final_verification_gate())
        state.open_questions = _dedupe_strings(state.open_questions + list(update.get("open_questions") or []))
        state.failed_paths = _dedupe_strings(state.failed_paths + list(update.get("failed_paths") or []))
        research_artifacts = dict(update.get("research_artifacts") or {})
        state.failed_paths = _dedupe_strings(state.failed_paths + list(research_artifacts.get("failed_paths") or []))
        state.open_questions = _dedupe_strings(state.open_questions + list(research_artifacts.get("subgoals") or []))
        if update.get("branch_updates"):
            state.branch_states = [dict(item) for item in list(update.get("branch_updates") or []) if isinstance(item, dict)]
        state.last_summary = str(update.get("summary") or "").strip()
        state.next_action = str(update.get("next_action") or "").strip()

        if previous_activity == "correction":
            state.correction_attempts += 1
        if previous_activity == "strengthening":
            state.strengthening_attempts += 1

        activity_status = str(update.get("activity_status") or "in_progress")
        if activity_status in {"checkpointed", "completed", "blocked"} and previous_activity not in state.completed_nodes:
            state.completed_nodes.append(previous_activity)

        next_stage, next_activity, stage_decision = self._resolve_next_stage_and_activity(state, update)
        state.stage = next_stage
        state.node = next_activity
        state.status = "completed" if stage_decision == "complete_project" and next_activity == "persistence" else "active"
        if update.get("next_action"):
            state.next_action = str(update.get("next_action") or "").strip()

        updated_files = self._write_structured_artifacts(update=update, state=state, session_id=session_id)
        checkpoint_meta = self.save_state(state, mirror_progress=False, checkpoint_reason="turn_update")

        payload = {
            "previous_stage": previous_stage,
            "stage": state.stage,
            "previous_node": previous_activity,
            "current_node": state.node,
            "previous_activity": previous_activity,
            "current_activity": state.node,
            "status": state.status,
            "activity_status": activity_status,
            "stage_decision": stage_decision,
            "selected_skills": list(state.selected_skills),
            "selected_tools": list(state.selected_tools),
            "trigger_rules_used": trigger_rules_used,
            "instruction_conflicts": instruction_conflicts,
            "state_assessment": dict(state.state_assessment),
            "intermediate_verification": dict(intermediate_verification),
            "final_verification_gate": dict(state.final_verification_gate),
            "summary": state.last_summary,
            "next_action": state.next_action,
            "updated_files": updated_files,
            "checkpoint": checkpoint_meta,
        }
        if self.session_store is not None:
            self.session_store.append_turn_event(
                session_id,
                {
                    "type": "research_workflow_updated",
                    "text": "Research workflow focus changed from %s to %s." % (previous_activity, state.node),
                    "created_at": utc_now(),
                    **payload,
                },
            )
        return payload

    def update_after_turn(
        self,
        *,
        project_slug: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> Dict[str, object]:
        """Refresh the research snapshot after one turn without any controller LLM."""
        payload = self.refresh_after_turn(
            project_slug=project_slug,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
        )
        try:
            archive_payload = self._archive_research_turn(
                project_slug=project_slug,
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            payload["research_log_archive"] = archive_payload
            if self.session_store is not None:
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "research_log_archived",
                        "text": "Archived %s research-log record(s)." % int(archive_payload.get("archived") or 0),
                        "created_at": utc_now(),
                        **archive_payload,
                    },
                )
        except Exception as exc:
            payload["research_log_archive"] = {"archived": 0, "error": str(exc)}
            if self.session_store is not None:
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "research_log_archive_error",
                        "text": str(exc),
                        "created_at": utc_now(),
                    },
                )
        return payload

    def archive_after_turn(
        self,
        *,
        project_slug: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        turn_context: Optional[List[Dict[str, object]]] = None,
    ) -> Dict[str, object]:
        """Archive one completed research turn without refreshing workflow state."""
        try:
            archive_payload = self._archive_research_turn(
                project_slug=project_slug,
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
                turn_context=turn_context,
            )
            if self.session_store is not None:
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "research_log_archived",
                        "text": "Archived %s research-log record(s)." % int(archive_payload.get("archived") or 0),
                        "created_at": utc_now(),
                        **archive_payload,
                    },
                )
            return archive_payload
        except Exception as exc:
            payload = {"archived": 0, "error": str(exc)}
            if self.session_store is not None:
                self.session_store.append_turn_event(
                    session_id,
                    {
                        "type": "research_log_archive_error",
                        "text": str(exc),
                        "created_at": utc_now(),
                    },
                )
            return payload
