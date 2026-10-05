"""Statistical candidate detection (architecture layer 4, Stage 3): answers "what changed?", nothing more."""

from pulseos.detection.candidates import AnomalyCandidate
from pulseos.detection.config import SERIES, DetectorConfig, SeriesSpec
from pulseos.detection.run import detect

__all__ = ["SERIES", "AnomalyCandidate", "DetectorConfig", "SeriesSpec", "detect"]
