import logging
from typing import Any, Dict, List

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FaceRecognizer:
    """InsightFace adapter that returns face boxes and normalized embeddings."""

    def __init__(self, model_name: str = "buffalo_l", det_size: tuple[int, int] = (640, 640)):
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError("InsightFace is required for face recognition") from exc

        self.app = FaceAnalysis(name=model_name, providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=-1, det_size=det_size)

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        faces = self.app.get(frame)
        detections = []
        for face in faces:
            x1, y1, x2, y2 = [int(value) for value in face.bbox]
            width = max(0, x2 - x1)
            height = max(0, y2 - y1)
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = face.embedding
            vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
            norm = np.linalg.norm(vector)
            if width and height and norm:
                detections.append({
                    "bbox": (x1, y1, width, height),
                    "embedding": (vector / norm).tolist(),
                })
        return detections


def save_face_crop(frame: np.ndarray, bbox: tuple[int, int, int, int], path: str) -> bool:
    x, y, width, height = bbox
    crop = frame[max(0, y):max(0, y) + height, max(0, x):max(0, x) + width]
    return bool(crop.size and cv2.imwrite(path, crop))
