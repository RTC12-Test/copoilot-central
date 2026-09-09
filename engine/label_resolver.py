from typing import List, Optional
from adapters import resolve_tech_from_label, get_adapter

def resolve_technology_from_labels(labels: List[str]) -> List[str]:
    """Given a list of ci_* labels, return the tech identifiers."""
    techs = []
    for label in labels:
        if label.startswith("ci_"):
            tech = resolve_tech_from_label(label)
            if tech and tech not in techs:
                techs.append(tech)
    return techs

def resolve_primary_technology(labels: List[str]) -> Optional[str]:
    """Return the first tech label or None."""
    techs = resolve_technology_from_labels(labels)
    return techs[0] if techs else None

def get_adapter_for_label(label: str):
    tech = resolve_tech_from_label(label)
    return get_adapter(tech)
