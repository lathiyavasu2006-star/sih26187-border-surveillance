"""Build a TensorRT FP16 engine from the pinned YOLOv8x weights.

    C:\\pythonjarvis\\python.exe -m ml.build_engine

The engine is specific to this GPU, TensorRT version and input size. A JSON sidecar records exactly what
it was built from; the detector refuses an engine whose sidecar does not match the pinned weights hash,
the current GPU or the installed TensorRT, and falls back to PyTorch instead.
"""
import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YOLO_AUTOINSTALL", "False")

from ml.config import ml_config  # noqa: E402
from ml.detector import file_sha256  # noqa: E402

logger = logging.getLogger("sih26187.ml.build_engine")


def engine_metadata_path(engine_path: str) -> Path:
    return Path(str(engine_path) + ".json")


def build(workspace_gb: float = 2.0) -> Path:
    import tensorrt
    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        raise SystemExit("CUDA GPU required to build a TensorRT engine")
    weights = Path(ml_config.model_path)
    if not weights.is_file():
        raise SystemExit(f"weights not found: {weights}")
    weights_hash = file_sha256(str(weights))
    if ml_config.model_sha256 and weights_hash != ml_config.model_sha256.lower():
        raise SystemExit(f"refusing to build from unpinned weights: {weights_hash} != {ml_config.model_sha256}")

    target = Path(ml_config.engine_path)
    started = time.perf_counter()
    logger.info("Building TensorRT %s FP16 engine from %s (imgsz=%d, workspace=%.1f GB) — this takes minutes",
                tensorrt.__version__, weights.name, ml_config.image_size, workspace_gb)
    exported = YOLO(str(weights)).export(format="engine", half=True, imgsz=ml_config.image_size, device=0,
                                         simplify=False, dynamic=False, workspace=workspace_gb, verbose=False)
    exported = Path(exported)
    if exported.resolve() != target.resolve():
        shutil.move(str(exported), str(target))
    onnx_leftover = weights.with_suffix(".onnx")
    if onnx_leftover.exists():
        onnx_leftover.unlink()  # intermediate export artefact

    metadata = {
        "engine_sha256": file_sha256(str(target)),
        "source_weights": weights.name,
        "source_sha256": weights_hash,
        "tensorrt_version": tensorrt.__version__,
        "gpu": torch.cuda.get_device_name(0),
        "image_size": ml_config.image_size,
        "half": True,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "build_seconds": round(time.perf_counter() - started, 1),
    }
    engine_metadata_path(str(target)).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("Engine ready: %s (%.1f MB) in %.0fs, sha256=%s", target, target.stat().st_size / 1e6,
                metadata["build_seconds"], metadata["engine_sha256"])
    return target


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="Build the YOLOv8x TensorRT engine")
    parser.add_argument("--workspace", type=float, default=2.0, help="TensorRT builder workspace in GB (4 GB GPU: <= 2)")
    args = parser.parse_args()
    build(args.workspace)


if __name__ == "__main__":
    main()
