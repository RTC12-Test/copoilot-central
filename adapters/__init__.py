from .base import BaseTechAdapter
from .terraform_adapter import TerraformAdapter
from .python_adapter import PythonAdapter
from .java_adapter import JavaAdapter
from .go_adapter import GoAdapter
from .rust_adapter import RustAdapter

REGISTRY = {
    "terraform": TerraformAdapter,
    "python": PythonAdapter,
    "java": JavaAdapter,
    "go": GoAdapter,
    "rust": RustAdapter,
}

def get_adapter(tech: str) -> BaseTechAdapter:
    cls = REGISTRY.get(tech)
    if not cls:
        raise KeyError(f"No adapter registered for technology: {tech}")
    return cls()

def resolve_tech_from_label(label: str) -> str:
    if label.startswith("ci_"):
        return label[3:]
    return label
