"""
Industry-specific **static** agent orderings (named graphs).

Each pipeline is a curated sequence of existing job-level agents over the job summary JSON.
This is the recommended baseline before dynamic LLM routing: explicit, testable, auditable.

Order rationale (examples):
- **Retail / LP-heavy**: synthesis → loss_prevention early for shrink narrative.
- **Healthcare**: privacy_review early given regulatory sensitivity.
- **Perimeter-heavy** verticals: perimeter_chain promoted after synthesis.
"""

from __future__ import annotations

from overwatch.models import AgentKind, IndustryPack

# Short aliases for readable pipeline definitions
S = AgentKind.synthesis
R = AgentKind.risk_review
I = AgentKind.incident_brief
C = AgentKind.compliance_brief
L = AgentKind.loss_prevention
P = AgentKind.perimeter_chain
V = AgentKind.privacy_review

INDUSTRY_PIPELINES: dict[IndustryPack, list[AgentKind]] = {
    IndustryPack.general: [S, R, I, C, L, P, V],
    IndustryPack.retail_qsr: [S, L, R, I, V, C, P],
    IndustryPack.logistics_warehouse: [S, C, P, R, I, V, L],
    IndustryPack.manufacturing: [S, C, R, I, P, V],
    IndustryPack.commercial_real_estate: [S, P, R, I, V, C],
    IndustryPack.transportation_hubs: [S, R, P, I, V, C, L],
    IndustryPack.critical_infrastructure: [S, P, R, C, I, V],
    IndustryPack.banking_atm: [S, L, R, V, I, C],
    IndustryPack.hospitality_venues: [S, L, R, I, V, C, P],
    IndustryPack.education_campus: [S, R, P, I, V, C],
    IndustryPack.healthcare_facilities: [S, V, R, C, I, P],
}


def pipeline_for(industry: IndustryPack) -> list[AgentKind]:
    """Return a **copy** of the agent sequence for this industry pack."""
    return list(INDUSTRY_PIPELINES[industry])
