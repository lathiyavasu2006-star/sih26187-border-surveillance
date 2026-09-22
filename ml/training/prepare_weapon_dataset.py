"""Turn the downloaded weapon datasets into one YOLO dataset the trainer can use.

Sources (all from https://github.com/ari-dasci/OD-WeaponDetection, CC BY 4.0, University of Granada):
  * "Pistol detection"      — 3 000 images, Pascal VOC boxes
  * "Knife_detection"       — ~2 000 images, Pascal VOC boxes
  * "Weapons and similar handled objects" (SOHAS) — already YOLO, and it also labels the everyday objects
    people hold (phone, wallet, card). Those stay in as their own classes on purpose: a detector that has
    never seen a phone in a hand calls it a pistol.

Only `firearm` and `knife` ever raise an alert; the other classes exist to teach the model the difference.

    python -m ml.training.prepare_weapon_dataset
"""
import argparse
import hashlib
import random
import re
import shutil
import sys
import xml.etree.ElementTree as ElementTree
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CLASSES = ["firearm", "knife", "phone", "wallet", "banknote", "card"]
ALERTING = ("firearm", "knife")

#: Every label spelling seen in the three sources, mapped onto our classes (Spanish names included).
LABEL_MAP = {
    "pistol": "firearm", "pistola": "firearm", "gun": "firearm", "handgun": "firearm", "arma": "firearm",
    "weapon": "firearm", "revolver": "firearm", "rifle": "firearm",
    "knife": "knife", "cuchillo": "knife", "navaja": "knife",
    "smartphone": "phone", "phone": "phone", "movil": "phone", "mobile": "phone", "telefono": "phone",
    "monedero": "wallet", "wallet": "wallet", "purse": "wallet",
    "billete": "banknote", "bill": "banknote", "banknote": "banknote",
    "tarjeta": "card", "card": "card", "creditcard": "card",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
SPLITS = (("train", 0.8), ("val", 0.1), ("test", 0.1))


#: File-name stems shared by unrelated photos (a scraped "pistol_0421" is not a frame of "pistol_0422").
#: Any other repeated stem — KravMagaTraining, DefenseKnifeAttack, ABbframe — is frames cut from one video.
INDEPENDENT_STEMS = {"pistol", "knife", "smartphone", "monedero", "billete", "tarjeta", "img", "dsc", "armas", "image"}


def split_key(image: Path) -> str:
    """What decides the split: frames of one video must all land in the same split, otherwise the test set is
    the training set one frame later and the scores are fiction."""
    stem = re.sub(r"[\s_()\-]*\d+[)\s]*$", "", image.stem).lower()
    # No source-folder prefix: SOHAS re-uses Knife_detection frames under the same names, and a frame must
    # land in one split whichever copy of it we read.
    if stem and stem not in INDEPENDENT_STEMS:
        return f"video:{stem}"
    return image.name.lower()


def split_for(name: str) -> str:
    """Deterministic split: the same image always lands in the same split, however often this is re-run."""
    digest = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    running = 0.0
    for split, share in SPLITS:
        running += share
        if digest < running:
            return split
    return SPLITS[-1][0]


def voc_boxes(xml_path: Path) -> Tuple[Optional[Tuple[int, int]], List[Tuple[str, float, float, float, float]]]:
    """(width, height), [(class, cx, cy, w, h) normalised] from a Pascal VOC file."""
    try:
        root = ElementTree.parse(xml_path).getroot()
    except ElementTree.ParseError:
        return None, []
    size = root.find("size")
    width = int(float(size.findtext("width", "0"))) if size is not None else 0
    height = int(float(size.findtext("height", "0"))) if size is not None else 0
    boxes = []
    for obj in root.findall("object"):
        raw = (obj.findtext("name") or "").strip().lower()
        name = LABEL_MAP.get(raw)
        box = obj.find("bndbox")
        if name is None or box is None or width <= 0 or height <= 0:
            continue
        x1, y1 = float(box.findtext("xmin", "0")), float(box.findtext("ymin", "0"))
        x2, y2 = float(box.findtext("xmax", "0")), float(box.findtext("ymax", "0"))
        x1, x2 = sorted((max(0.0, x1), min(float(width), x2)))
        y1, y2 = sorted((max(0.0, y1), min(float(height), y2)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        boxes.append((name, (x1 + x2) / 2 / width, (y1 + y2) / 2 / height, (x2 - x1) / width, (y2 - y1) / height))
    return (width, height), boxes


def find_image(stem: str, folders: List[Path]) -> Optional[Path]:
    for folder in folders:
        for suffix in IMAGE_SUFFIXES:
            candidate = folder / f"{stem}{suffix}"
            if candidate.exists():
                return candidate
    return None


def collect_voc(source: Path, images_dir: str, annotations_dir: str) -> List[Tuple[Path, List]]:
    image_folder, annotation_folder = source / images_dir, source / annotations_dir
    if not annotation_folder.is_dir():
        return []
    pairs = []
    for xml_path in sorted(annotation_folder.glob("*.xml")):
        image = find_image(xml_path.stem, [image_folder])
        if image is None:
            continue
        _size, boxes = voc_boxes(xml_path)
        if boxes:
            pairs.append((image, boxes))
    return pairs


def collect_yolo(root: Path) -> List[Tuple[Path, List]]:
    """SOHAS ships YOLO labels; its class order comes from the dataset.yaml next to them."""
    if not root.is_dir():
        return [], {}
    names: Dict[int, str] = {}
    for yaml_path in list(root.rglob("*.yaml")) + list(root.rglob("*.yml")):
        text = yaml_path.read_text(encoding="utf-8", errors="ignore")
        if "names" not in text:
            continue
        after = text.split("names", 1)[1]
        raw = after[after.find("[") + 1: after.find("]")] if "[" in after else ""
        for index, item in enumerate(part.strip().strip("'\"") for part in raw.split(",") if part.strip()):
            names[index] = item.lower()
        if names:
            break

    pairs = []
    for label_path in sorted(root.rglob("*.txt")):
        if "labels" not in label_path.parts:
            continue
        # .../labels/train/x.txt -> .../images/train/x.jpg, and .../labels/x.txt -> .../images/x.jpg
        images_root = Path(str(label_path.parent).replace("labels", "images", 1))
        image = find_image(label_path.stem, [images_root, images_root.parent])
        if image is None:
            continue
        boxes = []
        for line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            raw = names.get(int(float(parts[0])), "")
            name = LABEL_MAP.get(raw)
            if name is None:
                continue
            cx, cy, w, h = (float(value) for value in parts[1:])
            if w <= 0 or h <= 0:
                continue
            boxes.append((name, cx, cy, w, h))
        if boxes:
            pairs.append((image, boxes))
    return pairs, names


def deduplicate(pairs: List[Tuple[Path, List]]) -> List[Tuple[Path, List]]:
    """Drop byte-identical images. The sources overlap (3 000+ images appear twice), and a copy in train with
    its twin in test would make the test score measure memory, not detection."""
    best: Dict[str, Tuple[Path, List]] = {}
    for image, boxes in pairs:
        digest = hashlib.sha1(image.read_bytes()).hexdigest()
        kept = best.get(digest)
        # Prefer the richer labelling; on a tie the later source (SOHAS, which also labels phones etc.) wins.
        if kept is None or len(boxes) >= len(kept[1]):
            best[digest] = (image, boxes)
    removed = len(pairs) - len(best)
    print(f"duplicates removed: {removed} (kept {len(best)} unique images)")
    return list(best.values())


def clamp_box(cx: float, cy: float, w: float, h: float) -> Optional[Tuple[float, float, float, float]]:
    """Keep a normalised box inside the image; None when nothing usable is left."""
    x1, y1 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
    x2, y2 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
    if x2 - x1 <= 1e-4 or y2 - y1 <= 1e-4:
        return None
    return (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the YOLO weapon dataset")
    parser.add_argument("--source", default=str(PROJECT_ROOT / "datasets" / "weapons" / "files"))
    parser.add_argument("--out", default=str(PROJECT_ROOT / "datasets" / "weapons" / "yolo"))
    parser.add_argument("--link", action="store_true", help="hard-link images instead of copying (same drive only)")
    args = parser.parse_args()

    source, out = Path(args.source), Path(args.out)
    if not source.is_dir():
        print(f"Source folder not found: {source}")
        return 2

    pistols = collect_voc(source / "Pistol detection", "Weapons", "xmls")
    knives = collect_voc(source / "Knife_detection", "Images", "annotations")
    sohas_pairs, sohas_names = collect_yolo(source / "Weapons and similar handled objects" / "Sohas_weapon-Detection-YOLOv5")
    print(f"pistol detection: {len(pistols)} annotated images")
    print(f"knife detection:  {len(knives)} annotated images")
    print(f"sohas (YOLO):     {len(sohas_pairs)} annotated images, source classes {sorted(set(sohas_names.values()))}")

    everything = deduplicate(pistols + knives + sohas_pairs)
    if not everything:
        print("Nothing to prepare yet — is the download still running?")
        return 1

    for split, _ in SPLITS:
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    counts: Dict[str, Counter] = {split: Counter() for split, _ in SPLITS}
    images_per_split: Counter = Counter()
    seen = set()
    for image, boxes in everything:
        key = f"{image.parent.parent.name}/{image.name}"
        if key in seen:
            continue
        seen.add(key)
        # An image whose every box falls outside the frame would be written without a label: skip it first.
        clamped = [(name, clamp_box(cx, cy, w, h)) for name, cx, cy, w, h in boxes]
        boxes = [(name, *box) for name, box in clamped if box is not None]
        if not boxes:
            continue
        split = split_for(split_key(image))
        # Source folders repeat file names, so the copy keeps the dataset name as a prefix.
        target_name = f"{image.parent.parent.name.replace(' ', '_')}__{image.stem}"
        target_image = out / "images" / split / f"{target_name}{image.suffix.lower()}"
        if not target_image.exists():
            if args.link:
                try:
                    target_image.hardlink_to(image)
                except OSError:
                    shutil.copy2(image, target_image)
            else:
                shutil.copy2(image, target_image)
        lines = [f"{CLASSES.index(name)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for name, cx, cy, w, h in boxes]
        (out / "labels" / split / f"{target_name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        images_per_split[split] += 1
        counts[split].update(name for name, *_ in boxes)

    data_yaml = out / "data.yaml"
    data_yaml.write_text(
        "# SIH26187 weapon detection dataset\n"
        "# Built by ml/training/prepare_weapon_dataset.py from DaSCI OD-WeaponDetection (CC BY 4.0).\n"
        f"path: {out.as_posix()}\n"
        "train: images/train\nval: images/val\ntest: images/test\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n",
        encoding="utf-8",
    )

    print(f"\nwrote {data_yaml}")
    for split, _ in SPLITS:
        detail = ", ".join(f"{name} {counts[split][name]}" for name in CLASSES if counts[split][name])
        print(f"  {split:>5}: {images_per_split[split]:>5} images | boxes: {detail}")
    print(f"\nalerting classes: {', '.join(ALERTING)} (the rest exist to prevent false alarms)")
    return 0


if __name__ == "__main__":
    random.seed(26187)
    sys.exit(main())
