import copy
import hashlib
import re
from typing import Any


DEFAULT_MEMORY_ONTOLOGY = {
    "version": "ontology-guided-hypergraph-memory.v2",
    "node_types": [
        {
            "id": "memory",
            "label": "MemoryRecord",
            "description": "An episodic long-term memory item."
        },
        {
            "id": "entity",
            "label": "Entity",
            "description": "A named concept, person, tool, paper, file, model, or object."
        },
        {
            "id": "topic",
            "label": "Topic",
            "description": "A thematic grouping extracted from memory metadata or entities."
        },
        {
            "id": "session",
            "label": "ConversationSession",
            "description": "A conversation or runtime session that produced memory."
        },
        {
            "id": "project",
            "label": "Project",
            "description": "A project-level context or workspace."
        },
        {
            "id": "skill",
            "label": "Skill",
            "description": "A reusable agent capability."
        },
        {
            "id": "tool",
            "label": "Tool",
            "description": "A callable tool or script used by the agent."
        },
        {
            "id": "claim",
            "label": "Claim",
            "description": "A conclusion, judgment, or distilled statement."
        },
        {
            "id": "evidence",
            "label": "Evidence",
            "description": "A source, result, citation, log, or verification artifact."
        },
        {
            "id": "artifact",
            "label": "Artifact",
            "description": "A generated file, answer, video, document, or code change."
        },
        {
            "id": "owner",
            "label": "Owner",
            "description": "The user or actor that owns a memory scope."
        },
        {
            "id": "scope",
            "label": "Scope",
            "description": "The visibility or sharing boundary for memory."
        },
        {
            "id": "hyperedge",
            "label": "HyperEdge",
            "description": "A multi-party relation treated as a first-class node for graph display."
        },
        {
            "id": "paper",
            "label": "Paper",
            "description": "An academic paper, article, or research publication."
        },
        {
            "id": "author",
            "label": "Author",
            "description": "A person who authored or contributed to a paper."
        },
        {
            "id": "venue",
            "label": "Venue",
            "description": "A journal, conference, or publication venue."
        },
        {
            "id": "literature_review",
            "label": "LiteratureReview",
            "description": "A structured literature review or survey artifact."
        },
        {
            "id": "decision",
            "label": "Decision",
            "description": "A design or architectural decision made by a user or agent."
        },
        {
            "id": "alternative",
            "label": "Alternative",
            "description": "An option considered but not chosen in a decision."
        }
    ],
    "relation_types": [
        {
            "id": "member",
            "label": "member",
            "description": "Connects a hyperedge to one of its participants.",
            "source_type": "hyperedge",
            "target_type": "*"
        },
        {
            "id": "mentions",
            "label": "mentions",
            "description": "A memory mentions an entity.",
            "source_type": "memory",
            "target_type": "entity"
        },
        {
            "id": "topic",
            "label": "topic",
            "description": "A memory belongs to a topic.",
            "source_type": "memory",
            "target_type": "topic"
        },
        {
            "id": "related",
            "label": "related",
            "description": "A pairwise associative memory link.",
            "source_type": "*",
            "target_type": "*"
        },
        {
            "id": "produces",
            "label": "produces",
            "description": "A workflow or skill produces an artifact or claim.",
            "source_type": "*",
            "target_type": "artifact"
        },
        {
            "id": "uses",
            "label": "uses",
            "description": "A workflow uses a skill, tool, evidence item, or memory.",
            "source_type": "*",
            "target_type": "*"
        },
        {
            "id": "verified_by",
            "label": "verified_by",
            "description": "A claim or artifact is checked by evidence.",
            "source_type": "claim",
            "target_type": "evidence"
        },
        {
            "id": "belongs_to",
            "label": "belongs_to",
            "description": "A node belongs to a project, session, owner, or scope.",
            "source_type": "*",
            "target_type": "owner"
        },
        {
            "id": "cites",
            "label": "cites",
            "description": "A paper cites another paper as a reference.",
            "source_type": "paper",
            "target_type": "paper"
        },
        {
            "id": "authored_by",
            "label": "authored_by",
            "description": "A paper is authored by a person.",
            "source_type": "paper",
            "target_type": "author"
        },
        {
            "id": "published_in",
            "label": "published_in",
            "description": "A paper is published in a venue.",
            "source_type": "paper",
            "target_type": "venue"
        },
        {
            "id": "reviews",
            "label": "reviews",
            "description": "A literature review includes or references a paper.",
            "source_type": "literature_review",
            "target_type": "paper"
        },
        {
            "id": "surveys",
            "label": "surveys",
            "description": "A paper is a survey or review of another paper.",
            "source_type": "paper",
            "target_type": "paper"
        },
        {
            "id": "extends",
            "label": "extends",
            "description": "A paper extends or builds upon prior work.",
            "source_type": "paper",
            "target_type": "paper"
        },
        {
            "id": "decides",
            "label": "decides",
            "description": "An owner decides on a chosen option for a topic or question.",
            "source_type": "owner",
            "target_type": "decision"
        },
        {
            "id": "considers",
            "label": "considers",
            "description": "A decision considers an alternative option.",
            "source_type": "decision",
            "target_type": "alternative"
        },
        {
            "id": "conflicts_with",
            "label": "conflicts_with",
            "description": "Two decisions are in conflict (different owners chose different options for the same topic).",
            "source_type": "decision",
            "target_type": "decision"
        }
    ],
    "hyperedge_types": [
        {
            "id": "memory_capture",
            "label": "Memory Capture",
            "description": "Connects one memory to its session, owner, scope, topics, and entities.",
            "roles": [
                "episode",
                "session",
                "owner",
                "scope",
                "entity",
                "topic"
            ]
        },
        {
            "id": "task_experience",
            "label": "Task Experience",
            "description": "Connects a request, project, skills, tools, evidence, artifacts, and outcome.",
            "roles": [
                "request",
                "project",
                "skill",
                "tool",
                "evidence",
                "artifact",
                "outcome"
            ]
        },
        {
            "id": "skill_distillation",
            "label": "Skill Distillation",
            "description": "Connects successful traces, reusable procedure, tests, and the resulting skill.",
            "roles": [
                "trace",
                "procedure",
                "test",
                "skill",
                "project"
            ]
        },
        {
            "id": "literature_review",
            "label": "Literature Review",
            "description": "Connects a survey/review to its papers, authors, venues, research questions, claims, and evidence.",
            "roles": [
                "review",
                "paper",
                "author",
                "venue",
                "research_question",
                "claim",
                "evidence",
                "methodology",
                "finding"
            ]
        },
        {
            "id": "decision_record",
            "label": "Decision Record",
            "description": "Connects a decision to its owner, topic, chosen option, alternatives, project, and session.",
            "roles": [
                "decision",
                "owner",
                "topic",
                "chosen",
                "alternative",
                "project",
                "session"
            ]
        }
    ]
}


def default_memory_ontology() -> dict:
    return copy.deepcopy(DEFAULT_MEMORY_ONTOLOGY)


def normalize_ontology_label(value: Any) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    return clean or "unknown"


def ontology_node_id(kind: str, label: Any) -> str:
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(kind or "entity").strip().lower()).strip("_") or "entity"
    clean = normalize_ontology_label(label)
    digest = hashlib.sha1(clean.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{safe_kind}:{digest}"
