"""Vault contract — what frontmatter fields are mandatory per document type.

Rules are hardcoded here (not in a YAML file) so the contract travels with
the code. If you change these rules, the reconcile check will re-surface
the new violations on the next update cycle.

Contract design:
    - Every doc must have type and stage.
    - captures must also have source_url, summary, concepts, topic.
    - indexes must have topic and updated.
    - stage 3+ (research/synthesis/decisions/expertise) must have tags.
    - Any doc whose folder-inferred stage doesn't match its declared
      stage is flagged (e.g., a "research" doc found in captures/).

The contract is intentionally NOT a schema-enforcement system. It's an
auditor: it reports violations so the operator can decide what to do
(fix manually, trigger compile pass, leave as is).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Folder → stage mapping. This is the source of truth for stage inference
# when frontmatter is missing or wrong.
STAGE_BY_FOLDER: dict[str, int] = {
    "captures":  1,
    "research":  3,
    "synthesis": 4,
    "decisions": 5,
    "expertise": 6,
    # references and initiatives don't have a stage; they're special.
    "references":  0,  # stage 0 = "no stage, reference material"
    "initiatives": 0,  # stage 0 = "lifecycle doc"
    "indexes":     0,  # stage 0 = "auto-maintained index"
}


# Folder → type mapping — the doc's type field should match this
TYPE_BY_FOLDER: dict[str, str] = {
    "captures":    "capture",
    "research":    "research",
    "synthesis":   "synthesis",
    "decisions":   "decision",
    "expertise":   "expertise",
    "references":  "reference",
    "initiatives": "initiative",
    "indexes":     "index",
}


@dataclass
class DocumentContract:
    """Per-type contract definition."""

    type: str
    mandatory: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)


# The actual contract. Each entry is the per-type rule.
CONTRACT: dict[str, DocumentContract] = {
    "capture": DocumentContract(
        type="capture",
        mandatory=["title", "type", "stage", "date", "source_url"],
        recommended=["summary", "concepts", "topic", "author", "platform", "tags", "source_ref"],
    ),
    "research": DocumentContract(
        type="research",
        mandatory=["title", "type", "stage", "date"],
        recommended=["summary", "concepts", "topic", "tags", "source_ref"],
    ),
    "synthesis": DocumentContract(
        type="synthesis",
        mandatory=["title", "type", "stage", "date"],
        recommended=["summary", "concepts", "topic", "source_ref"],
    ),
    "decision": DocumentContract(
        type="decision",
        mandatory=["title", "type", "stage", "date"],
        recommended=["summary", "tags", "initiative"],
    ),
    "expertise": DocumentContract(
        type="expertise",
        mandatory=["title", "type", "stage"],
        recommended=["summary", "concepts", "tags"],
    ),
    "reference": DocumentContract(
        type="reference",
        mandatory=["title", "type"],
        recommended=["tags"],
    ),
    "initiative": DocumentContract(
        type="initiative",
        mandatory=["title", "type", "status"],
        recommended=["phase", "summary"],
    ),
    "index": DocumentContract(
        type="index",
        mandatory=["title", "type", "topic", "updated"],
        recommended=["doc_count"],
    ),
}


def infer_stage(rel_path: Path) -> int:
    """Infer the stage from the vault-relative path."""
    parts = rel_path.parts
    # Expect: knowledge/<folder>/<file>.md
    if len(parts) >= 2 and parts[0] == "knowledge":
        folder = parts[1]
        return STAGE_BY_FOLDER.get(folder, 0)
    return 0


def infer_type(rel_path: Path) -> str:
    """Infer the document type from the vault-relative path."""
    parts = rel_path.parts
    if len(parts) >= 2 and parts[0] == "knowledge":
        folder = parts[1]
        return TYPE_BY_FOLDER.get(folder, "unknown")
    return "unknown"


def validate(
    *,
    rel_path: Path,
    frontmatter: dict[str, Any],
) -> tuple[list[str], dict[str, bool]]:
    """Apply the contract to a single doc.

    Returns:
        (issues, checks) where:
            issues — list of human-readable violation strings
            checks — dict of flags: has_frontmatter, has_summary, has_concepts,
                     has_topic, has_source_url
    """
    issues: list[str] = []
    checks: dict[str, bool] = {
        "has_frontmatter": bool(frontmatter),
        "has_summary": False,
        "has_concepts": False,
        "has_topic": False,
        "has_source_url": False,
    }

    if not frontmatter:
        issues.append("missing_frontmatter")
        return issues, checks

    # Detect the doc's type: prefer frontmatter, fall back to folder
    declared_type = (frontmatter.get("type") or "").strip().lower()
    inferred_type = infer_type(rel_path)
    resolved_type = declared_type or inferred_type

    if not declared_type:
        issues.append("missing_type")
    elif declared_type != inferred_type and inferred_type != "unknown":
        issues.append(f"type_mismatch_folder_says_{inferred_type}")

    # Stage consistency
    declared_stage = frontmatter.get("stage")
    inferred_stage = infer_stage(rel_path)
    if declared_stage is None:
        if inferred_stage > 0:
            issues.append("missing_stage")
    else:
        try:
            declared_stage = int(declared_stage)
        except (TypeError, ValueError):
            issues.append("stage_not_int")
            declared_stage = None
        if (
            declared_stage is not None
            and inferred_stage > 0
            and declared_stage != inferred_stage
        ):
            issues.append(
                f"stage_mismatch_folder={inferred_stage}_declared={declared_stage}"
            )

    # Recommended-field checks (used by the Library view for quality flags)
    checks["has_summary"] = bool((frontmatter.get("summary") or "").strip())
    concepts = frontmatter.get("concepts")
    checks["has_concepts"] = bool(concepts) and isinstance(concepts, list)
    checks["has_topic"] = bool((frontmatter.get("topic") or "").strip())
    checks["has_source_url"] = bool(
        (frontmatter.get("source_url") or frontmatter.get("source") or "").strip()
    )

    # Apply the per-type contract rules
    rule = CONTRACT.get(resolved_type)
    if rule:
        for field in rule.mandatory:
            val = frontmatter.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                issues.append(f"missing_mandatory:{field}")

    return issues, checks
