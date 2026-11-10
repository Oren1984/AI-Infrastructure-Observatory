"""Evidence, governance, timeline and cost catalogs (local, versioned fixtures)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from .config import DATA_DIR

VerificationStatus = Literal["verified_primary", "partially_verified", "secondary_only", "not_verified", "not_applicable"]
GAP_STATUSES = {"secondary_only", "not_verified"}


class _M(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}


class PublishedValue(_M):
    name: str
    value: float
    unit: str


class Source(_M):
    id: str
    title: str
    publisher: str
    url: str | None
    edition: str
    reference_date: str | None
    verification_date: str | None
    verification_status: VerificationStatus
    classification: Literal["published", "assumption", "simulated"]
    used_for: str
    excerpt: str | None
    values: list[PublishedValue] = []

    @property
    def is_gap(self) -> bool:
        return self.verification_status in GAP_STATUSES


class SourceCatalog(_M):
    catalog_version: str
    sources: list[Source]

    def by_id(self) -> dict[str, Source]:
        return {s.id: s for s in self.sources}


class Jurisdiction(_M):
    code: str
    name: str
    initial: bool
    relation: str


class Control(_M):
    id: str
    title: str
    demonstration: str


class Policy(_M):
    id: str
    title: str
    control: str
    owner: str


class GovernanceEntry(_M):
    id: str
    jurisdiction: str
    level: Literal["supranational", "national", "subnational"]
    issuing_authority: str
    instrument: str
    source_id: str
    section: str | None
    publication_date: str | None
    effective_date: str | None
    domain: Literal["facility", "ai_service"]
    legal_status: Literal["binding", "official_guidance", "voluntary_standard"]
    scope: str
    applicability: Literal["applies", "may_apply", "comparison_only", "unknown"]
    related_control: str
    evidence_expected: str
    uncertainty: str | None


class GovernanceCatalog(_M):
    catalog_version: str
    jurisdictions: list[Jurisdiction]
    controls: list[Control]
    policies: list[Policy]
    entries: list[GovernanceEntry]

    @model_validator(mode="after")
    def _refs(self) -> "GovernanceCatalog":
        codes = {j.code for j in self.jurisdictions}
        controls = {c.id for c in self.controls}
        for e in self.entries:
            if e.jurisdiction not in codes:
                raise ValueError(f"{e.id}: unknown jurisdiction {e.jurisdiction}")
            if e.related_control not in controls:
                raise ValueError(f"{e.id}: unknown control {e.related_control}")
        for p in self.policies:
            if p.control not in controls:
                raise ValueError(f"{p.id}: unknown control {p.control}")
        return self


class Milestone(_M):
    date: str
    category: Literal["planning", "site_selection", "permits", "utility_connection", "construction",
                      "commissioning", "go_live", "expansion", "regulation", "market"]
    evidence: Literal["observed", "announced", "hypothetical"]
    title: str
    source_id: str


class Timeline(_M):
    catalog_version: str
    milestones: list[Milestone]


class CostItem(_M):
    category: str
    value: float


class CostBlock(_M):
    period: str
    unit: str
    items: list[CostItem]


class Operating(_M):
    electricity: dict
    diesel_eur_per_l: float
    water_eur_per_m3: float
    generator_maintenance_eur_per_run_hour: float
    period: str


class Costs(_M):
    catalog_version: str
    currency: str
    price_basis: str
    location: str
    classification: str
    source_id: str
    operating: Operating
    capital: CostBlock
    annual_fixed_operating: CostBlock


class Catalogs(_M):
    sources: SourceCatalog
    governance: GovernanceCatalog
    timeline: Timeline
    costs: Costs

    @model_validator(mode="after")
    def _source_refs(self) -> "Catalogs":
        ids = self.sources.by_id()
        refs = [e.source_id for e in self.governance.entries] + [m.source_id for m in self.timeline.milestones] + [self.costs.source_id]
        missing = sorted({r for r in refs if r not in ids})
        if missing:
            raise ValueError(f"unknown source ids: {missing}")
        return self

    def coverage(self) -> list[dict]:
        """Per-jurisdiction coverage: verified entries vs. explicit gaps."""
        src = self.sources.by_id()
        rows = []
        for j in self.governance.jurisdictions:
            entries = [e for e in self.governance.entries if e.jurisdiction == j.code]
            verified = [e for e in entries if src[e.source_id].verification_status in ("verified_primary", "partially_verified")]
            gaps = [e for e in entries if src[e.source_id].is_gap]
            if verified and not gaps:
                status = "Verified entries"
            elif verified:
                status = "Verified entries + marked gaps"
            elif gaps:
                status = "Coverage gap (marked)"
            else:
                status = "No entries"
            rows.append({"code": j.code, "jurisdiction": j.name, "initial": j.initial, "relation": j.relation,
                         "entries": len(entries), "verified": len(verified), "gaps": len(gaps), "status": status})
        return rows


def _y(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_catalogs(directory: Path | None = None) -> Catalogs:
    d = directory or DATA_DIR / "catalog"
    return Catalogs(
        sources=SourceCatalog.model_validate(_y(d / "sources.yaml")),
        governance=GovernanceCatalog.model_validate(_y(d / "governance.yaml")),
        timeline=Timeline.model_validate(_y(d / "timeline.yaml")),
        costs=Costs.model_validate(_y(d / "costs.yaml")),
    )
