from app.classifier.ai_engine import AiEngine
from app.classifier.rule_engine import RuleEngine


def test_ai_json_to_result_accepts_hiring_task():
    result = AiEngine._to_result({
        "accepted": True,
        "score": 88,
        "category": "web",
        "technologies": ["React", "Node.js"],
        "budget": "$800",
        "task_type": "hiring",
        "reason": "Paid React developer needed",
    })
    assert result.accepted
    assert result.score == 88
    assert result.classifier == "ai"
    assert result.budget_hint == "$800"
    assert "hiring" in result.reason
    assert "React" in result.technologies


def test_ai_json_to_result_rejects_low_score():
    result = AiEngine._to_result({
        "accepted": True,
        "score": 40,
        "category": None,
        "technologies": [],
        "budget": None,
        "task_type": None,
        "reason": "Too vague",
    })
    assert not result.accepted
    assert result.confidence == "REJECTED"


def test_ai_accepts_without_budget():
    result = AiEngine._to_result({
        "accepted": True,
        "score": 72,
        "category": "web",
        "technologies": ["React"],
        "budget": None,
        "task_type": "hiring",
        "reason": "Looking for a React developer, no budget stated",
    })
    assert result.accepted
    assert result.budget_hint is None


def test_prefilter_asks_ai_for_taskish_message():
    engine = RuleEngine("config/keywords.yaml", threshold=70, high_threshold=90)
    assert engine.should_ask_ai("Need a developer to build a Discord bot, paid.")


def test_prefilter_skips_obvious_scam():
    engine = RuleEngine("config/keywords.yaml", threshold=70, high_threshold=90)
    assert not engine.should_ask_ai(
        "Guaranteed crypto profits, send wallet seed phrase to get started."
    )
