from assistant_shared.models import TaskStep

WORKFLOW_STEPS = (
    TaskStep.read,
    TaskStep.analyze,
    TaskStep.patch,
    TaskStep.test,
    TaskStep.review,
    TaskStep.summarize,
)
