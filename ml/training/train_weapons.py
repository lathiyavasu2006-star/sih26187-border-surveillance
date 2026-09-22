"""Fine-tune a YOLO detector for weapons, and report honestly how well it did.

The live pipeline keeps YOLOv8x for people and vehicles; this is a second, much smaller model that only
looks at the region around a detected person, which is why a compact model is enough and why it fits
alongside the main one on a 4 GB laptop GPU.

    python -m ml.training.prepare_weapon_dataset     # once, after the dataset download
    python -m ml.training.train_weapons              # ~2-4 h on an RTX 3050 laptop
    python -m ml.training.train_weapons --evaluate-only models/weapon_yolov8s.pt

The trained weights are copied to models/weapon_yolov8s.pt with a SHA-256 sidecar, the same way the main
model is pinned, so a swapped file is noticed.
"""
import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_YAML = PROJECT_ROOT / "datasets" / "weapons" / "yolo" / "data.yaml"
RUNS = PROJECT_ROOT / "datasets" / "weapons" / "runs"
TARGET_WEIGHTS = PROJECT_ROOT / "models" / "weapon_yolov8s.pt"


class StayAwake:
    """Keeps Windows from sleeping while training runs, without touching the power plan.

    SetThreadExecutionState is the same request a video player makes: it lasts only as long as this process,
    so an idle-sleep timer of a few minutes no longer kills a multi-hour run."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __enter__(self):
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
        return self

    def __exit__(self, *_exc):
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def report(metrics, names, title: str) -> dict:
    """Per-class precision / recall / mAP — the numbers that decide whether this is demo-ready."""
    print(f"\n{title}")
    print(f"{'class':>10} {'precision':>10} {'recall':>8} {'mAP50':>8} {'mAP50-95':>9}")
    summary = {}
    for index, name in names.items():
        try:
            precision, recall, map50, map95 = metrics.box.class_result(index)
        except (IndexError, TypeError):
            continue
        summary[name] = {"precision": round(float(precision), 4), "recall": round(float(recall), 4),
                         "mAP50": round(float(map50), 4), "mAP50_95": round(float(map95), 4)}
        print(f"{name:>10} {precision:>10.3f} {recall:>8.3f} {map50:>8.3f} {map95:>9.3f}")
    print(f"{'ALL':>10} {metrics.box.mp:>10.3f} {metrics.box.mr:>8.3f} {metrics.box.map50:>8.3f} {metrics.box.map:>9.3f}")
    summary["_all"] = {"precision": round(float(metrics.box.mp), 4), "recall": round(float(metrics.box.mr), 4),
                       "mAP50": round(float(metrics.box.map50), 4), "mAP50_95": round(float(metrics.box.map), 4)}
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Train the weapon detector")
    parser.add_argument("--model", default=str(PROJECT_ROOT / "models" / "yolov8s.pt"), help="starting weights (yolov8n/s/m)")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8, help="8 fits 4 GB with yolov8s at 640; -1 = auto")
    parser.add_argument("--workers", type=int, default=2, help="data loader processes (Windows: keep low)")
    parser.add_argument("--fraction", type=float, default=1.0, help="share of the training set to use (smoke test)")
    parser.add_argument("--patience", type=int, default=20, help="stop when validation stops improving")
    parser.add_argument("--device", default="0")
    parser.add_argument("--name", default=None)
    parser.add_argument("--evaluate-only", default=None, help="skip training and score these weights")
    args = parser.parse_args()

    if not DATA_YAML.exists():
        print(f"{DATA_YAML} is missing — run: python -m ml.training.prepare_weapon_dataset")
        return 2

    from ultralytics import YOLO

    if args.evaluate_only:
        model = YOLO(args.evaluate_only)
        metrics = model.val(data=str(DATA_YAML), split="test", imgsz=args.imgsz, device=args.device, verbose=False,
                            project=str(RUNS), name="evaluate", exist_ok=True)
        report(metrics, model.names, f"test split — {args.evaluate_only}")
        return 0

    run_name = args.name or f"weapons_{Path(args.model).stem}_{datetime.now().strftime('%m%d_%H%M')}"
    print(f"training {args.model} on {DATA_YAML} for up to {args.epochs} epochs (batch {args.batch}, {args.imgsz} px)")
    started = time.time()
    model = YOLO(args.model)
    with StayAwake():
        _train(model, args, run_name)
    minutes = (time.time() - started) / 60
    return _finish(args, run_name, minutes)


def _train(model, args, run_name: str) -> None:
    model.train(
        data=str(DATA_YAML),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,
        device=args.device,
        project=str(RUNS),
        name=run_name,
        exist_ok=True,
        pretrained=True,
        optimizer="auto",
        seed=26187,
        val=True,
        plots=True,
        # A weapon is small and often half-hidden by the hand holding it: keep scale/translate jitter, but
        # never flip the image vertically — CCTV footage is never upside down.
        flipud=0.0,
        fliplr=0.5,
        degrees=5.0,
        scale=0.5,
        mosaic=1.0,
        close_mosaic=10,
        workers=args.workers,
        fraction=args.fraction,
        cache=False,
    )


def _finish(args, run_name: str, minutes: float) -> int:
    from ultralytics import YOLO

    best = RUNS / run_name / "weights" / "best.pt"
    if not best.exists():
        print("Training finished without producing weights")
        return 3

    trained = YOLO(str(best))
    # Plots go next to the run, never into ./runs at the repository root.
    validation = trained.val(data=str(DATA_YAML), split="val", imgsz=args.imgsz, device=args.device, verbose=False,
                             project=str(RUNS / run_name), name="final_val", exist_ok=True)
    test = trained.val(data=str(DATA_YAML), split="test", imgsz=args.imgsz, device=args.device, verbose=False,
                       project=str(RUNS / run_name), name="final_test", exist_ok=True)
    scores = {"val": report(validation, trained.names, "validation split"),
              "test": report(test, trained.names, "test split (never seen during training)")}

    if args.fraction < 1.0:
        # A smoke test trains on a sliver of the data: its weights must never reach the live pipeline.
        print(f"\nsmoke test ({args.fraction:.0%} of the data, {minutes:.1f} min): weights left in {best.parent}, not installed")
        return 0

    TARGET_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, TARGET_WEIGHTS)
    sidecar = {
        "weights_sha256": sha256(TARGET_WEIGHTS),
        "base_model": Path(args.model).name,
        "classes": list(trained.names.values()),
        "dataset": "DaSCI OD-WeaponDetection (CC BY 4.0), prepared by ml/training/prepare_weapon_dataset.py",
        "epochs_requested": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "training_minutes": round(minutes, 1),
        "metrics": scores,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "run": str((RUNS / run_name).as_posix()),
    }
    TARGET_WEIGHTS.with_suffix(".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    print(f"\nweights -> {TARGET_WEIGHTS} ({minutes:.0f} min)\nsidecar -> {TARGET_WEIGHTS.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
