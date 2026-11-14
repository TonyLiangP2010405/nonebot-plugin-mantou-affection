from __future__ import annotations

from pathlib import Path

from .group_quiz import GroupQuizCoordinator, PendingGroupQuiz, QuizSpec

BUNDLED_TEYVAT_QUIZ_PATH = (
    Path(__file__).parent / "resources" / "teyvat_quiz_questions.json"
)

TEYVAT_QUIZ_SPEC = QuizSpec(
    prefix="🌍 提瓦特问答！",
    intro="馒头翻开了提瓦特的史书，考考你们……",
    right_reward=3,
    wrong_penalty=-5,
    timeout_penalty=-2,
    timeout_attr="mantou_affection_teyvat_timeout",
    log_label="提瓦特问答",
)

PendingTeyvatQuiz = PendingGroupQuiz


class TeyvatQuizCoordinator(GroupQuizCoordinator):
    """管理提瓦特知识问答，出题与结算流程与群友问答一致。"""

    def __init__(self, service, config, library=None, *, events=None, rng=None):
        super().__init__(
            service,
            config,
            library,
            events=events,
            rng=rng,
            spec=TEYVAT_QUIZ_SPEC,
        )
