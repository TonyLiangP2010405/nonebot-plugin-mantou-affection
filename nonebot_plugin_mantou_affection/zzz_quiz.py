from __future__ import annotations

from pathlib import Path

from .group_quiz import GroupQuizCoordinator, PendingGroupQuiz, QuizSpec

BUNDLED_ZZZ_QUIZ_PATH = (
    Path(__file__).parent / "resources" / "zzz_quiz_questions.json"
)

ZZZ_QUIZ_SPEC = QuizSpec(
    prefix="📺 绝区零问答！",
    intro="馒头调出了新艾利都的绳网档案，考考你们……",
    right_reward=3,
    wrong_penalty=-5,
    timeout_penalty=-2,
    timeout_attr="mantou_affection_zzz_timeout",
    log_label="绝区零问答",
)

PendingZzzQuiz = PendingGroupQuiz


class ZzzQuizCoordinator(GroupQuizCoordinator):
    """管理绝区零知识问答，出题与结算流程与群友问答一致。"""

    def __init__(self, service, config, library=None, *, events=None, rng=None):
        super().__init__(
            service,
            config,
            library,
            events=events,
            rng=rng,
            spec=ZZZ_QUIZ_SPEC,
        )
