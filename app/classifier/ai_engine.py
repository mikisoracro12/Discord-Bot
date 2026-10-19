import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from app.classifier.models import ClassificationResult

log = logging.getLogger("discord-task-finder.ai")

SYSTEM_PROMPT = """You classify Discord messages for a software-task finder bot.

Accept ONLY messages that are:
- Paid or clearly paid-intent freelance / contract / commission software work, OR
- Hiring requests for developers / programmers / engineers to build or fix software

Accept examples: Discord/Telegram bots, websites, web apps, SaaS, scripts, APIs,
mobile apps, Chrome extensions, automation, backend/frontend, bug fixes with pay.

Reject:
- Unpaid collaboration, equity-only, cofounder searches
- Scams, crypto trading, "send crypto", MLM
- Non-software jobs (translation, design-only, marketing-only, VA)
- Vague chat with no real development task
- Recruiting spam with no concrete build/fix request

Return JSON only, no markdown:
{
  "accepted": boolean,
  "score": integer 0-100,
  "category": string or null,
  "technologies": string[],
  "budget": string or null,
  "task_type": "hiring" | "freelance" | "commission" | "other" | null,
  "reason": short string
}

category should be one of:
discord_bot, telegram_bot, web, saas, mobile, browser_extension,
automation, api, game, data, other, or null.
"""


class AiEngine:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 20.0,
    ):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
        )

    async def classify(self, message: str) -> ClassificationResult:
        content = message.strip()
        if len(content) > 4000:
            content = content[:4000]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=0.1,
                max_tokens=400,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
            )
            raw = response.choices[0].message.content or "{}"
            data = self._parse_json(raw)
            return self._to_result(data)
        except Exception:
            log.exception("AI classification failed")
            raise

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        # Some Ollama models wrap thinking; strip common fences/tags first.
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    @staticmethod
    def _to_result(data: dict) -> ClassificationResult:
        try:
            score = int(data.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))
        accepted = bool(data.get("accepted")) and score >= 60

        technologies = data.get("technologies") or []
        if not isinstance(technologies, list):
            technologies = []
        technologies = [str(t) for t in technologies if t]

        category = data.get("category")
        if category is not None:
            category = str(category)

        confidence = (
            "HIGH" if accepted and score >= 90
            else "MEDIUM" if accepted
            else "REJECTED"
        )
        reason = str(data.get("reason") or "")
        task_type = data.get("task_type")
        if task_type:
            reason = f"[{task_type}] {reason}".strip()

        return ClassificationResult(
            accepted=accepted,
            score=score,
            confidence=confidence,
            category=category,
            technologies=technologies,
            matched_positive=["ai"],
            matched_negative=[],
            reason=reason or ("Accepted by AI" if accepted else "Rejected by AI"),
            classifier="ai",
            budget_hint=_optional_str(data.get("budget")),
        )


def _optional_str(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
