from __future__ import annotations

from pathlib import Path

from .group_quiz import GroupQuizCoordinator, PendingGroupQuiz, QuizSpec

BUNDLED_HSR_QUIZ_PATH = (
    Path(__file__).parent / "resources" / "hsr_quiz_questions.json"
)

HSR_QUIZ_SPEC = QuizSpec(
    prefix="🚄 星穹铁道问答！",
    intro="馒头翻开了星穹列车的航行日志，考考你们……",
    right_reward=3,
    wrong_penalty=-5,
    timeout_penalty=-2,
    timeout_attr="mantou_affection_hsr_timeout",
    log_label="星穹铁道问答",
)

PendingHsrQuiz = PendingGroupQuiz


class HsrQuizCoordinator(GroupQuizCoordinator):
    """管理星穹铁道知识问答，出题与结算流程与群友问答一致。"""

    def __init__(self, service, config, library=None, *, events=None, rng=None):
        super().__init__(
            service,
            config,
            library,
            events=events,
            rng=rng,
            spec=HSR_QUIZ_SPEC,
        )
