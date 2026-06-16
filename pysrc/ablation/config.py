"""Ablation experiment configuration.

Defines the experiments, queries, and metrics for systematic evaluation
of OpenMegatron's subsystems.
"""

# ═══════════════════════════════════════════════════════════════
#  Experiment Groups
# ═══════════════════════════════════════════════════════════════

# Group 1: RAG System Ablation
# Tests the contribution of each Tri-Store RAG component
RAG_ABLATIONS = {
    "full_rag": {
        "description": "Full Tri-Store RAG (PostgreSQL + Neo4j + Redis)",
        "components_enabled": ["pgvector", "fulltext", "neo4j", "redis_cache", "deterministic_ner"],
    },
    "pgvector_only": {
        "description": "PostgreSQL vector search only — no fulltext, no Neo4j, no cache",
        "components_enabled": ["pgvector"],
    },
    "pgvector_fulltext": {
        "description": "PostgreSQL hybrid search — vector + fulltext, no Neo4j",
        "components_enabled": ["pgvector", "fulltext"],
    },
    "pgvector_neo4j": {
        "description": "PostgreSQL vector + Neo4j graph — no fulltext, no cache",
        "components_enabled": ["pgvector", "neo4j"],
    },
    "full_no_cache": {
        "description": "Full RAG without Redis cache — every query hits database",
        "components_enabled": ["pgvector", "fulltext", "neo4j", "deterministic_ner"],
    },
    "llm_ner_only": {
        "description": "LLM-based entity extraction only — no deterministic regex NER",
        "components_enabled": ["pgvector", "fulltext", "neo4j", "redis_cache"],
    },
}

# Group 2: Memory Ontology Ablation
# Tests the impact of ontology alignment
MEMORY_ABLATIONS = {
    "ontology_aligned": {
        "description": "Full ontology-aligned memory (23 node types + 26 relation types)",
        "ontology_enabled": True,
    },
    "no_ontology": {
        "description": "Free-form memory — no ontology constraints on entities/relations",
        "ontology_enabled": False,
    },
}

# Group 3: Companion AI Ablation
# Tests the impact of companion model subsystems
COMPANION_ABLATIONS = {
    "full_companion": {
        "description": "Full companion AI: judge + visual + inference",
        "judge_enabled": True,
        "visual_enabled": True,
        "inference_enabled": True,
    },
    "judge_only": {
        "description": "Judge (reward scoring) only — no visual or inference companion",
        "judge_enabled": True,
        "visual_enabled": False,
        "inference_enabled": False,
    },
    "rule_scoring_only": {
        "description": "Rule-based scoring — no learned reward model",
        "judge_enabled": False,
        "visual_enabled": False,
        "inference_enabled": False,
    },
    "no_companion_routing": {
        "description": "No companion model routing — always uses cloud LLM",
        "judge_enabled": True,
        "visual_enabled": True,
        "inference_enabled": False,
    },
}

# Group 4: Training Data Ablation
# Tests the impact of external log import
TRAINING_ABLATIONS = {
    "all_sources": {
        "description": "Training data from all sources (External Text Agent + ExternalAgentJSONL + OpenClaw + self)",
        "sources": ["agent_text", "external_agent_jsonl", "openclaw", "self"],
    },
    "self_only": {
        "description": "Training data from self-play only",
        "sources": ["self"],
    },
    "external_only": {
        "description": "Training data from external logs only",
        "sources": ["agent_text", "external_agent_jsonl", "openclaw"],
    },
    "agent_text_only": {
        "description": "Training data from external text-agent logs only",
        "sources": ["agent_text"],
    },
}


# ═══════════════════════════════════════════════════════════════
#  Benchmark Query Sets
# ═══════════════════════════════════════════════════════════════

BENCHMARK_QUERIES = {
    # Fact lookup — should work with minimal RAG
    "fact_lookup": [
        "What databases does OpenMegatron use?",
        "How many tool types does the agent support?",
        "What is the memory ontology ID format?",
        "List the three companion AI subsystems.",
        "What is the default embedding dimension?",
        "How many slide layouts does PPT Master support?",
        "What GUI actions are available?",
        "What file types can RAG ingest?",
    ],

    # Relation queries — need Neo4j graph
    "relation": [
        "How does the RAG system use Neo4j?",
        "What is the relationship between memory and ontology?",
        "How does trajectory collection connect to model training?",
        "Explain how the visual flywheel feeds into the companion model.",
        "How does the dehydrator interact with the ontology?",
        "What edges connect RAG entities to document chunks?",
        "How does the dual-system router classify tasks?",
        "Trace the data flow from log import to model deployment.",
    ],

    # Synthesis queries — need full Tri-Store
    "synthesis": [
        "Summarize the complete OpenMegatron architecture.",
        "Compare the three companion AI subsystems.",
        "How does OpenMegatron improve upon GraphRAG and LightRAG?",
        "What makes the ontology-aligned memory different from traditional vector DBs?",
        "Explain the self-training closed loop.",
        "How does the system balance cost and quality?",
        "What are the key innovations of OpenMegatron?",
        "Describe the end-to-end pipeline from user input to model deployment.",
    ],

    # Multi-hop queries — need graph traversal
    "multi_hop": [
        "If a document mentions 'Neo4j', what entities will be created and how do they connect?",
        "How does a failed GUI action affect the companion model training pipeline?",
        "Trace the path of a External Agent JSONL log entry through the entire system.",
        "What happens when a companion model fails — trace the full fallback chain.",
        "How does changing the ontology definition affect existing memory data?",
        "Walk through the complete lifecycle of a user query from input to answer.",
    ],
}


# ═══════════════════════════════════════════════════════════════
#  Metrics Definitions
# ═══════════════════════════════════════════════════════════════

METRICS = {
    # Retrieval quality
    "retrieval_precision": "Precision@5 — fraction of retrieved chunks relevant to query",
    "retrieval_recall": "Recall@5 — fraction of relevant chunks retrieved",
    "retrieval_mrr": "Mean Reciprocal Rank — position of first relevant result",
    "answer_faithfulness": "Fraction of answer claims grounded in retrieved context",
    "answer_relevance": "Semantic relevance of answer to original query",

    # Performance
    "latency_ms": "End-to-end query latency in milliseconds",
    "cache_hit_rate": "Fraction of queries served from Redis cache",
    "token_usage": "Total LLM tokens consumed per query",
    "entity_count": "Number of entities extracted per document",

    # Cost
    "estimated_cost_usd": "Estimated API cost per query (USD)",

    # Agent
    "task_success": "Whether the task completed successfully",
    "tool_calls_count": "Number of tool calls per task",
    "retry_count": "Number of retries due to errors",
}
