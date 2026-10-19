from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ClassificationResult:
    accepted: bool
    score: int
    confidence: str
    category: Optional[str] = None
    technologies: list[str] = field(default_factory=list)
    matched_positive: list[str] = field(default_factory=list)
    matched_negative: list[str] = field(default_factory=list)
    reason: str = ""
