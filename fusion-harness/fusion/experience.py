"""Experience memory — cross-run experience cards with match-scored retrieval.

Python port of gah experience-memory.mjs. Cards record causal claims with
preconditions; retrieval scores by phase/control_domain/target_role match.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LESSON_TYPES = ("confirmed_effective", "confirmed_no_effect", "candidate")


@dataclass
class ExperienceCard:
    card_id: str
    game_id: str
    claim: str
    precondition: dict
    action: dict
    lesson_type: str
    confidence: float
    observed_delta: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    supporting_run_ids: list = field(default_factory=list)
    contradicting_run_ids: list = field(default_factory=list)
    invalidated: bool = False

    @staticmethod
    def make_id(game_id: str, claim: str, precondition: dict, action: dict) -> str:
        raw = json.dumps(
            {"game_id": game_id, "claim": claim, "precondition": precondition, "action": action},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def to_dict(self) -> dict:
        return {
            "schema_version": "agent_harness.experience_card.v1",
            "card_id": self.card_id,
            "game_id": self.game_id,
            "claim": self.claim,
            "precondition": self.precondition,
            "action": self.action,
            "lesson_type": self.lesson_type,
            "confidence": self.confidence,
            "observed_delta": self.observed_delta,
            "evidence_refs": self.evidence_refs,
            "supporting_run_ids": self.supporting_run_ids,
            "contradicting_run_ids": self.contradicting_run_ids,
            "invalidated": self.invalidated,
        }


def _match_score(card: ExperienceCard, ctx: dict) -> float:
    if card.invalidated:
        return -100.0
    score = card.confidence
    pre = card.precondition
    if pre.get("phase") is not None and pre.get("phase") == ctx.get("phase"):
        score += 5
    elif pre.get("phase") is not None:
        score -= 2
    if pre.get("control_domain") is not None and pre.get("control_domain") == ctx.get("control_domain"):
        score += 3
    elif pre.get("control_domain") is not None:
        score -= 2
    if pre.get("target_role") is not None and pre.get("target_role") == ctx.get("target_role"):
        score += 2
    elif pre.get("target_role") is not None:
        score -= 1
    if card.lesson_type in ("confirmed_effective", "confirmed_no_effect"):
        score += 2
    return score


class ExperienceMemory:
    def __init__(self, store_path: Path | None = None) -> None:
        self._cards: dict[str, ExperienceCard] = {}
        self._store_path = store_path
        if store_path and store_path.exists():
            self._load()

    def _load(self) -> None:
        for line in self._store_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            self._cards[data["card_id"]] = ExperienceCard(**{
                k: v for k, v in data.items()
                if k in ExperienceCard.__dataclass_fields__
            })

    def _save(self, card: ExperienceCard) -> None:
        if self._store_path:
            with open(self._store_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(card.to_dict(), ensure_ascii=False) + "\n")

    def add_or_update(
        self, *, game_id: str, claim: str, precondition: dict, action: dict,
        lesson_type: str, confidence: float, observed_delta: dict | None = None,
        run_id: str | None = None,
    ) -> ExperienceCard:
        card_id = ExperienceCard.make_id(game_id, claim, precondition, action)
        existing = self._cards.get(card_id)
        if existing:
            existing.lesson_type = lesson_type
            existing.confidence = max(existing.confidence, confidence)
            if run_id and run_id not in existing.supporting_run_ids:
                existing.supporting_run_ids.append(run_id)
            card = existing
        else:
            card = ExperienceCard(
                card_id=card_id, game_id=game_id, claim=claim,
                precondition=precondition, action=action,
                lesson_type=lesson_type, confidence=confidence,
                observed_delta=observed_delta or {},
                supporting_run_ids=[run_id] if run_id else [],
            )
            self._cards[card_id] = card
        self._save(card)
        return card

    def record_trial(
        self, *, game_id: str, option: str, parameters: dict,
        successes: int, failures: int, run_id: str | None = None,
        precondition: dict | None = None,
    ) -> ExperienceCard | None:
        """Derive a lesson from an experiment trial (gah experienceRecordFromTrial)."""
        total = successes + failures
        if total == 0:
            return None
        failure_rate = failures / total
        claim = f"{option} against context produced {'effect' if successes >= failures else 'no effect'}"
        if successes >= 2 and failure_rate <= 0.25:
            lesson = "confirmed_effective"
        elif failures >= 2 and failure_rate >= 0.5:
            lesson = "confirmed_no_effect"
        else:
            lesson = "candidate"
        return self.add_or_update(
            game_id=game_id, claim=claim,
            precondition=precondition or {},
            action={"option": option, "parameter_pattern": _strip_positional(parameters)},
            lesson_type=lesson,
            confidence=abs(0.5 - failure_rate) + 0.3 if lesson != "candidate" else 0.3,
            run_id=run_id,
        )

    def retrieve(self, ctx: dict, limit: int = 5) -> list[ExperienceCard]:
        scored = sorted(
            (c for c in self._cards.values()),
            key=lambda c: _match_score(c, ctx),
            reverse=True,
        )
        return [c for c in scored if _match_score(c, ctx) >= 2][:limit]

    def operator_constraints(self, minimum_confidence: float = 0.8,
                             minimum_runs: int = 2) -> list[dict]:
        """Compile verified cards into shadow operator constraints."""
        out = []
        for card in self._cards.values():
            if (
                card.lesson_type == "candidate"
                or card.confidence < minimum_confidence
                or len(card.supporting_run_ids) < minimum_runs
            ):
                continue
            out.append({
                "card_id": card.card_id,
                "claim": card.claim,
                "action": card.action,
                "lesson_type": card.lesson_type,
                "mode": "shadow",
                "enforcement_authorized": False,
            })
        return out


def _strip_positional(parameters: dict) -> dict:
    """Remove position/coordinate params so the pattern generalises."""
    blocked = {"target_id", "waypoint", "waypoints", "point", "from", "to"}
    return {k: v for k, v in parameters.items() if k not in blocked}
