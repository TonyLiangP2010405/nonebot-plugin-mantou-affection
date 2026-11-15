from __future__ import annotations

from pathlib import Path

from .group_quiz import GroupQuizCoordinator, PendingGroupQuiz, QuizSpec

BUNDLED_PETIT_PLANET_QUIZ_PATH = (
    Path(__file__).parent / "resources" / "petit_planet_quiz_questions.json"
)

PETIT_PLANET_QUIZ_SPEC = QuizSpec(
    prefix="🌱 星布谷地问答！",
    intro="馒头从星湾捎回一颗露卡种子，考考你们……",
    right_reward=3,
    wrong_penalty=-5,
    timeout_penalty=-2,
    timeout_attr="mantou_affection_petit_planet_timeout",
    log_label="星布谷地问答",
)

PendingPetitPlanetQuiz = PendingGroupQuiz


class PetitPlanetQuizCoordinator(GroupQuizCoordinator):
    """管理星布谷地知识问答，出题与结算流程与群友问答一致。"""

    def __init__(self, service, config, library=None, *, events=None, rng=None):
        super().__init__(
            service,
            config,
            library,
            events=events,
            rng=rng,
            spec=PETIT_PLANET_QUIZ_SPEC,
        )
