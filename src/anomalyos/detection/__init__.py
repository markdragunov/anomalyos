"""Statistical candidate detection (architecture layer 4, Stage 3): answers "what changed?", nothing more."""

from anomalyos.detection.candidates import AnomalyCandidate
from anomalyos.detection.config import SERIES, DetectorConfig, SeriesSpec
from anomalyos.detection.run import detect

__all__ = ["SERIES", "AnomalyCandidate", "DetectorConfig", "SeriesSpec", "detect"]
