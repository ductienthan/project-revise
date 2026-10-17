import uuid

from sqlalchemy.orm import Session

from preflight.core.errors import DomainError, NotFound
from preflight.db.models import Failure, Prediction, Project


def predict(db: Session, project: Project, hypothesis: str, evidence: str, confidence: int) -> Prediction:
    if not hypothesis.strip():
        raise DomainError("A prediction needs a hypothesis.")
    if not 0 <= confidence <= 100:
        raise DomainError("Confidence is a percentage from 0 to 100.")
    prediction = Prediction(user_id=project.user_id, project_id=project.id, hypothesis=hypothesis.strip(),
                            evidence_planned=evidence.strip(), confidence=confidence)
    project.predictions.append(prediction)
    db.flush()
    return prediction


def get_prediction(db: Session, user_id: uuid.UUID, prediction_id: uuid.UUID) -> Prediction:
    prediction = db.get(Prediction, prediction_id)
    if prediction is None or prediction.user_id != user_id:
        raise NotFound("prediction not found")
    return prediction


def resolve(db: Session, prediction: Prediction, correct: bool, failure_id: uuid.UUID | None) -> None:
    if prediction.correct is not None:
        raise DomainError("This prediction is already closed.")
    if failure_id is not None:
        failure = db.get(Failure, failure_id)
        if failure is None or failure.project_id != prediction.project_id:
            raise DomainError("That failure is not in this prediction's project.")
    prediction.correct, prediction.failure_id = correct, failure_id
