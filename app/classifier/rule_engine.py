import re
from pathlib import Path
import yaml

from app.classifier.models import ClassificationResult

DEFAULT_CONFIG = Path("config/keywords.yaml")

class RuleEngine:
    def __init__(self, config_path=DEFAULT_CONFIG, threshold=70, high_threshold=90):
        self.threshold = threshold
        self.high_threshold = high_threshold
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

    @staticmethod
    def normalize(text: str) -> str:
        text = text.lower().replace("’", "'")
        text = re.sub(r"[\u2010-\u2015]", "-", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def phrase_matches(text: str, phrases: list[str]) -> list[str]:
        return [p for p in phrases if p.lower() in text]

    def classify(self, message: str) -> ClassificationResult:
        text = self.normalize(message)

        positives = []
        negatives = []

        task = self.phrase_matches(text, self.cfg.get("task_request_phrases", []))
        software = self.phrase_matches(text, self.cfg.get("software_keywords", []))
        actions = self.phrase_matches(text, self.cfg.get("development_actions", []))
        collaboration = self.phrase_matches(text, self.cfg.get("collaboration_phrases", []))
        recruiting = self.phrase_matches(text, self.cfg.get("recruiting_phrases", []))
        scam = self.phrase_matches(text, self.cfg.get("scam_phrases", []))
        payment = self.phrase_matches(text, self.cfg.get("payment_phrases", []))

        positives.extend(task)
        positives.extend(software)
        positives.extend(actions)
        positives.extend(payment)
        negatives.extend(collaboration)
        negatives.extend(recruiting)
        negatives.extend(scam)

        # Strong phrase-level exclusions take priority for non-development opportunities.
        if scam or collaboration or recruiting:
            # A legitimate technical task can survive a generic technology word,
            # but explicit scam/recruiting/collaboration phrases are strong negatives.
            strong_scam = bool(scam)
            score = 0
            score += min(len(task), 2) * 30
            score += min(len(software), 4) * 15
            score += min(len(actions), 3) * 10
            score += min(len(payment), 2) * 5
            score -= min(len(collaboration), 2) * 45
            score -= min(len(recruiting), 2) * 45
            score -= min(len(scam), 2) * 60

            # Explicit "developer to build..." can override a weak generic negative,
            # but obvious scam-only messages remain rejected.
            if strong_scam and not (task and actions and software):
                return ClassificationResult(
                    accepted=False, score=score, confidence="REJECTED",
                    matched_positive=positives, matched_negative=negatives,
                    reason="Excluded/scam/recruiting/collaboration pattern detected."
                )

        score = 0
        score += min(len(task), 2) * 30
        score += min(len(software), 4) * 15
        score += min(len(actions), 3) * 10
        score += min(len(payment), 2) * 5
        score -= min(len(collaboration), 2) * 45
        score -= min(len(recruiting), 2) * 45
        score -= min(len(scam), 2) * 60

        # Budget is optional bonus only — never required.
        if re.search(r"(?:[$£€]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:usd|eur|gbp))", text):
            score += 10

        # Task + software is enough; actions help borderline cases without budget.
        if task and software and actions:
            score = max(score, self.threshold)

        # Require both task intent and software evidence. Payment/budget not required.
        accepted = bool(task) and bool(software) and score >= self.threshold
        confidence = (
            "HIGH" if accepted and score >= self.high_threshold
            else "MEDIUM" if accepted
            else "REJECTED"
        )

        category = self.detect_category(text)
        technologies = self.detect_technologies(text)

        reason = (
            "Strong software task signals detected."
            if accepted else
            "Insufficient evidence of a specific software-development task."
        )

        return ClassificationResult(
            accepted=accepted,
            score=score,
            confidence=confidence,
            category=category,
            technologies=technologies,
            matched_positive=positives,
            matched_negative=negatives,
            reason=reason,
            classifier="rules",
        )

    def should_ask_ai(
        self,
        message: str,
        rule_result: ClassificationResult | None = None,
        prefilter_threshold: int = 25,
    ) -> bool:
        """Cheap gate so obvious junk never hits the AI API."""
        result = rule_result or self.classify(message)
        if result.accepted:
            return True

        text = self.normalize(message)
        scam = self.phrase_matches(text, self.cfg.get("scam_phrases", []))
        collaboration = self.phrase_matches(text, self.cfg.get("collaboration_phrases", []))
        recruiting = self.phrase_matches(text, self.cfg.get("recruiting_phrases", []))
        task = self.phrase_matches(text, self.cfg.get("task_request_phrases", []))
        software = self.phrase_matches(text, self.cfg.get("software_keywords", []))
        actions = self.phrase_matches(text, self.cfg.get("development_actions", []))
        payment = self.phrase_matches(text, self.cfg.get("payment_phrases", []))

        if scam and not (task and software):
            return False
        if (collaboration or recruiting) and not (task and (software or actions)):
            return False

        soft_score = result.score
        soft_score += min(len(task), 2) * 10
        soft_score += min(len(software), 3) * 8
        soft_score += min(len(actions), 2) * 8
        soft_score += min(len(payment), 2) * 5
        return soft_score >= prefilter_threshold or bool(task and (software or actions))

    def detect_category(self, text: str) -> str | None:
        best = None
        best_hits = 0
        for category, terms in self.cfg.get("categories", {}).items():
            hits = sum(1 for term in terms if term.lower() in text)
            if hits > best_hits:
                best = category
                best_hits = hits
        return best

    def detect_technologies(self, text: str) -> list[str]:
        known = [
            "python", "javascript", "typescript", "java", "c#", "c++", "go",
            "rust", "php", "ruby", "kotlin", "swift", "react", "next.js",
            "vue", "angular", "node.js", "express", "django", "fastapi",
            "flask", "laravel", ".net", "postgresql", "mysql", "mongodb",
            "docker", "aws", "discord.py", "discord.js", "stripe", "ios",
            "android", "graphql"
        ]
        return [item for item in known if item in text]
