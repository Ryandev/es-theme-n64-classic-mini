"""``n64mini-compare-theme``: render (or load) a screenshot, diff it against the reference.

Two modes:
  --theme-dir DIR   Tier-1 headless render of the theme's grid view, then compare
  --actual PNG      compare an existing screenshot (e.g. a Tier-2 live capture)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from n64mini.harness.metrics import Metrics, compare
from n64mini.harness.render import render_grid_view
from n64mini.harness.report import write_report
from n64mini.paths import GOLDEN_REFERENCE, SCREENSHOTS_DIR


def _load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as f:
        return f.convert("RGB")


def run(
    *, reference: Path, out_dir: Path, theme_dir: Path | None = None, actual: Path | None = None
) -> Metrics:
    """Render or load the actual image, diff it, and write the report and metrics."""
    if theme_dir is not None and actual is not None:
        raise ValueError("pass only one of theme_dir or actual")
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_img = _load_rgb(reference)

    wireframe: Image.Image | None = None
    if theme_dir is not None:
        rendered = render_grid_view(theme_dir / "theme.xml")
        actual_img, wireframe = rendered.clean, rendered.wireframe
        actual_img.save(out_dir / "xml_render_clean.png")
        wireframe.save(out_dir / "xml_render_wireframe.png")
    elif actual is not None:
        actual_img = _load_rgb(actual)
    else:
        raise ValueError("pass one of theme_dir or actual")

    diff, metrics = compare(actual_img, ref_img)
    write_report(
        out_dir / "visual_diff_report.png",
        reference=ref_img,
        reference_name=reference.name,
        actual=actual_img,
        wireframe=wireframe,
        diff=diff,
        metrics=metrics,
    )
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--theme-dir", type=Path, help="theme folder containing theme.xml")
    source.add_argument("--actual", type=Path, help="existing 1280x720 screenshot PNG")
    parser.add_argument("--reference", type=Path, default=GOLDEN_REFERENCE)
    parser.add_argument("--out-dir", type=Path, default=SCREENSHOTS_DIR)
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=None,
        help="exit 1 if overall similarity %% is below this",
    )
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir
    min_similarity: float | None = args.min_similarity

    metrics = run(
        reference=args.reference, out_dir=out_dir, theme_dir=args.theme_dir, actual=args.actual
    )

    overall = metrics["overall"]
    print(f"report:  {out_dir / 'visual_diff_report.png'}")
    print(f"overall: {overall['similarity_pct']:.2f}% (MAE={overall['mae']:.2f})")
    for name, zone in metrics["zones"].items():
        print(
            f"  {name:<28} {zone['similarity_pct']:6.2f}%  "
            f"(exact<=8: {zone['within_tol_pct']:6.2f}%, MAE={zone['mae']:.2f})"
        )
    if min_similarity is not None and overall["similarity_pct"] < min_similarity:
        print(f"FAILED: below --min-similarity {min_similarity:.2f}%")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
