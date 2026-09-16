from typing import List
from adapters import resolve_tech_from_label


def resolve_technology_from_labels(labels: List[str]) -> List[str]:
    """Given a list of ci_* labels, return the tech identifiers."""
    techs = []
    for label in labels:
        if label.startswith("ci_"):
            tech = resolve_tech_from_label(label)
            if tech and tech not in techs:
                techs.append(tech)
    return techs