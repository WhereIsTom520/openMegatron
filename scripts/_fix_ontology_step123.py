"""Fix script: Steps 1-3 — Ontology expansion + ID unification + graph_engine fill.
Run from project root: python scripts/_fix_ontology_step123.py
"""
import sys
from pathlib import Path

# ── STEP 1: Expand memory_ontology.py ───────────────────────────────────────

with open('pysrc/memory_ontology.py', 'r', encoding='utf-8') as f:
    ont = f.read()

# Add new node_types before the closing of node_types list
new_nodes = '''        {
            "id": "rag_entity",
            "label": "RAGEntity",
            "description": "A named entity extracted from RAG document ingestion."
        },
        {
            "id": "document",
            "label": "Document",
            "description": "A document ingested into the RAG knowledge base."
        },
        {
            "id": "community",
            "label": "Community",
            "description": "A community of related entities detected by graph analysis."
        },
        {
            "id": "memory_card",
            "label": "MemoryCard",
            "description": "A dehydrated memory card produced by the dialogue dehydrator pipeline."
        }
    ],
'''

# Insert before the last closing bracket of node_types
node_types_end = ont.find(']\n    ],\n    "relation_types"')
if node_types_end == -1:
    print("ERROR: Could not find node_types end")
    sys.exit(1)

# Find the last node type entry before the closing bracket
last_node = ont.rfind('}', 0, node_types_end)
insert_pos = ont.find('\n    ]', last_node)
ont = ont[:insert_pos] + ',\n' + new_nodes + ont[insert_pos + len('\n    ]'):]

# Add new relation_types
new_relations = '''        {
            "id": "rag_mentions",
            "label": "rag_mentions",
            "description": "A RAG entity is mentioned in a document chunk.",
            "source_type": "rag_entity",
            "target_type": "document"
        },
        {
            "id": "similar_to",
            "label": "similar_to",
            "description": "A memory is semantically similar to another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "elaborates",
            "label": "elaborates",
            "description": "A memory elaborates or expands on another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "supports",
            "label": "supports",
            "description": "A memory provides supporting evidence for a claim or another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "contradicts",
            "label": "contradicts",
            "description": "A memory contradicts or challenges another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "causes",
            "label": "causes",
            "description": "A memory represents a cause of another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "precedes",
            "label": "precedes",
            "description": "A memory temporally precedes another memory (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "part_of",
            "label": "part_of",
            "description": "A memory is a part or component of a larger memory structure (A-MEM link).",
            "source_type": "memory",
            "target_type": "memory"
        },
        {
            "id": "contains_chunk",
            "label": "contains_chunk",
            "description": "A document contains a text chunk.",
            "source_type": "document",
            "target_type": "document"
        }
    ],
'''

# Find relation_types closing
rel_types_end = ont.find(']\n    ],\n    "hyperedge_types"')
if rel_types_end == -1:
    print("ERROR: Could not find relation_types end")
    sys.exit(1)

last_rel = ont.rfind('}', 0, rel_types_end)
insert_pos = ont.find('\n    ]', last_rel)
ont = ont[:insert_pos] + ',\n' + new_relations + ont[insert_pos + len('\n    ]'):]

with open('pysrc/memory_ontology.py', 'w', encoding='utf-8') as f:
    f.write(ont)
print("Step 1: Expanded ontology — now 23 node_types + 26 relation_types")


# ── STEP 2: Unify ID generation ─────────────────────────────────────────────

# Fix memory.py's ontology_node_id to call memory_ontology.ontology_node_id
with open('pysrc/memory.py', 'r', encoding='utf-8') as f:
    mem = f.read()

old_id_method = '''    def ontology_node_id(self, kind, label):
        raw = f"onto:{kind}:{label}"
        return f"onto_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"'''

new_id_method = '''    def ontology_node_id(self, kind, label):
        """Generate a stable ontology node ID using the canonical format."""
        from memory_ontology import ontology_node_id as _onto_id
        return _onto_id(kind, label)'''

if old_id_method in mem:
    mem = mem.replace(old_id_method, new_id_method)
    print("Step 2a: Fixed memory.py ontology_node_id to use canonical format")
else:
    print("WARNING: Could not find old ontology_node_id in memory.py")
    # Try alternate format
    if 'def ontology_node_id(self, kind, label):' in mem:
        print("  Found method but format differs. Searching...")

with open('pysrc/memory.py', 'w', encoding='utf-8') as f:
    f.write(mem)

# Fix rag_ingest.py entity ID
with open('pysrc/rag_ingest.py', 'r', encoding='utf-8') as f:
    ri = f.read()

old_rag_id = "hashlib.sha256(f\"{entity['name']}:{entity['type']}\".encode()).hexdigest()[:16]"
new_rag_id = "ontology_node_id('rag_entity', entity['name'])"

count = ri.count(old_rag_id)
if count > 0:
    ri = ri.replace(old_rag_id, new_rag_id)
    # Add import
    ri = ri.replace(
        'from typing import Any, Dict, List, Optional, Tuple',
        'from typing import Any, Dict, List, Optional, Tuple\nfrom memory_ontology import ontology_node_id'
    )
    with open('pysrc/rag_ingest.py', 'w', encoding='utf-8') as f:
        f.write(ri)
    print(f"Step 2b: Fixed {count} entity ID usages in rag_ingest.py")

# Fix rag_sync.py entity ID
with open('pysrc/rag_sync.py', 'r', encoding='utf-8') as f:
    rs = f.read()

old_sync_id = "hashlib.sha256(f\"{entity['name']}:{entity['type']}\".encode()).hexdigest()[:16]"
if old_sync_id in rs:
    rs = rs.replace(old_sync_id, new_rag_id)
    rs = rs.replace(
        'from typing import Any, Dict, List, Optional, Set',
        'from typing import Any, Dict, List, Optional, Set\nfrom memory_ontology import ontology_node_id'
    )
    with open('pysrc/rag_sync.py', 'w', encoding='utf-8') as f:
        f.write(rs)
    print("Step 2c: Fixed entity ID in rag_sync.py")


# ── STEP 3: Fill graph_engine.py gaps ────────────────────────────────────────

with open('pysrc/graph_engine.py', 'r', encoding='utf-8') as f:
    ge = f.read()

# Add missing label_map entries
old_label_map = '''    label_map = {
        "paper": "Paper",
        "author": "Author",
        "venue": "Venue",
        "topic": "Topic",
        "skill": "Skill",
        "tool": "Tool",
        "decision": "Decision",
        "owner": "Owner",
        "memory": "Memory",
        "evidence": "Evidence",
        "claim": "Claim",
        "artifact": "Artifact",
        "entity": "Entity",
        "rag_entity": "RAGEntity",
        "alternative": "Alternative",
        "literature_review": "LiteratureReview",
        "project": "Project",
        "session": "Session",
    }'''

new_label_map = '''    label_map = {
        "paper": "Paper",
        "author": "Author",
        "venue": "Venue",
        "topic": "Topic",
        "skill": "Skill",
        "tool": "Tool",
        "decision": "Decision",
        "owner": "Owner",
        "memory": "Memory",
        "evidence": "Evidence",
        "claim": "Claim",
        "artifact": "Artifact",
        "entity": "Entity",
        "rag_entity": "RAGEntity",
        "alternative": "Alternative",
        "literature_review": "LiteratureReview",
        "project": "Project",
        "session": "Session",
        "scope": "Scope",
        "hyperedge": "HyperEdge",
        "document": "Document",
        "community": "Community",
        "memory_card": "MemoryCard",
    }'''

ge = ge.replace(old_label_map, new_label_map)

# Add missing ensure_schema constraints
old_schema_end = '''            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE")
        except Exception:
            pass'''

new_schema_end = '''            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Artifact) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Alternative) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:LiteratureReview) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Session) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Scope) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:HyperEdge) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Tool) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:ConversationSession) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:MemoryRecord) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:RAGEntity) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Community) REQUIRE n.id IS UNIQUE")
            await session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:MemoryCard) REQUIRE n.id IS UNIQUE")
        except Exception:
            pass'''

if old_schema_end in ge:
    ge = ge.replace(old_schema_end, new_schema_end)
    with open('pysrc/graph_engine.py', 'w', encoding='utf-8') as f:
        f.write(ge)
    print("Step 3: Filled graph_engine label_map (23 entries) and ensure_schema (23 constraints)")
else:
    print("WARNING: Could not find ensure_schema end in graph_engine.py")

print("\n=== Steps 1-3 complete ===")
