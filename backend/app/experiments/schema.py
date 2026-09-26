from pydantic import BaseModel
from datetime import datetime

class ExperimentRun(BaseModel):
    name: str
    category: str
    description: str
    metrics: dict[str, float]
    timestamp: datetime = None