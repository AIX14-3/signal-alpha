"""Episodic memory for the aggregation orchestrator (에이전트화 Wave 3 / Stage 2).

A published signal = one *episode*: which sources fired, in which direction, at
what (rule) score, with a short evidence summary. ``EpisodeWriter`` embeds that
"situation" and upserts it into ``signal_episodes``; ``EpisodeRecall`` embeds the
*current* situation and pulls the cosine-nearest past episodes so the
orchestrator's judge can reference "우리 제품 자신의 이력" — never the model's
priors, and **never** the headline number.

Invariants (mirrors the plan §4-A / §6):
  - Recall is a *reference for reasoning/needs_review calibration only*. It does
    NOT feed the headline score/direction — those stay owned by the meta-learner.
  - Every embedding/DB path degrades silently: an embed failure or missing table
    leaves publishing untouched (writer is best-effort, recall returns []).
"""

from app.memory.episode_outcome import EpisodeOutcomeRecorder
from app.memory.episode_recall import EpisodeRecall, RecalledEpisode
from app.memory.episode_writer import EpisodeWriter
from app.memory.outcome_task import EpisodeOutcomeTaskHandler
from app.memory.situation import SituationInput, build_situation_text

__all__ = [
    "EpisodeOutcomeRecorder",
    "EpisodeOutcomeTaskHandler",
    "EpisodeRecall",
    "EpisodeWriter",
    "RecalledEpisode",
    "SituationInput",
    "build_situation_text",
]
