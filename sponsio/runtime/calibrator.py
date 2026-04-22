"""Per-model confidence calibration.

LLM judges are typically not calibrated out of the box — a "0.8
confidence" from GPT-4o doesn't reliably map to an 80% true-positive
rate. This module provides a per-model piecewise-linear mapping from
raw score → calibrated score, keyed on ``model_name``.

The MVP behaviour is **identity passthrough**: unfitted models return
``raw`` unchanged. This is intentional — β thresholds in contracts
describe nominal (not operational) confidence until real calibration
data exists.

Data flow:

1. Run the judge in shadow mode, logging ``(model, raw, true_label)``
   tuples to ``~/.sponsio/calibration_samples.jsonl``.
2. Offline, run :meth:`ModelCalibrator.fit` (requires ``scikit-learn``,
   an optional dep) to fit isotonic regression and persist a per-model
   piecewise-linear map to ``~/.sponsio/calibration.json``.
3. At runtime, :meth:`calibrate` loads the map and applies it.

Step 2 is deferred post-MVP — we collect samples now, fit when there's
enough data. Until then, step 3 is identity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_CALIBRATION_PATH = Path("~/.sponsio/calibration.json")


class ModelCalibrator:
    """Loads per-model piecewise-linear calibration maps from disk.

    Each map is a list of ``(raw, calibrated)`` breakpoints sorted by
    ``raw``. :meth:`calibrate` linearly interpolates between adjacent
    breakpoints; values outside the fitted range are clipped to the
    endpoint.

    Args:
        path: JSON file storing ``{model_name: [[raw, cal], ...]}``. If
            the file doesn't exist, all lookups are identity.
    """

    def __init__(self, path: str | os.PathLike = DEFAULT_CALIBRATION_PATH):
        self._path = Path(os.path.expanduser(str(path)))
        self._maps: dict[str, list[tuple[float, float]]] = self._load()

    def _load(self) -> dict[str, list[tuple[float, float]]]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        out: dict[str, list[tuple[float, float]]] = {}
        for model, pts in raw.items():
            # Normalize to sorted list of (raw, cal) float tuples.
            tuples = [(float(x), float(y)) for x, y in pts]
            tuples.sort(key=lambda p: p[0])
            out[model] = tuples
        return out

    def calibrate(self, model_name: str, raw: float) -> float:
        """Return the calibrated confidence for ``model_name`` given ``raw``.

        Unfitted models pass through unchanged (identity). This keeps
        BooleanJudge usable before any calibration data exists.
        """
        pts = self._maps.get(model_name)
        if not pts:
            return raw
        # Clip to fitted range
        if raw <= pts[0][0]:
            return pts[0][1]
        if raw >= pts[-1][0]:
            return pts[-1][1]
        # Linear interpolation between the surrounding breakpoints.
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= raw <= x1:
                if x1 == x0:
                    return y0
                return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
        return raw  # unreachable given the clipping branches above

    def fit(
        self,
        model_name: str,
        raw_scores: list[float],
        true_labels: list[bool],
    ) -> None:
        """Fit isotonic regression on ``(raw_scores, true_labels)`` and
        persist the resulting piecewise-linear map.

        Requires the ``scikit-learn`` optional dependency. Install with::

            pip install 'sponsio[calibration]'

        Args:
            model_name: Key to store the fitted map under.
            raw_scores: List of uncalibrated judge scores in [0, 1].
            true_labels: List of ground-truth booleans (True = positive,
                the event the judge is supposed to detect).

        Raises:
            ImportError: If ``scikit-learn`` isn't installed.
            ValueError: On input-length mismatch.
        """
        if len(raw_scores) != len(true_labels):
            raise ValueError(
                f"raw_scores and true_labels must have same length, "
                f"got {len(raw_scores)} and {len(true_labels)}"
            )
        try:
            from sklearn.isotonic import IsotonicRegression  # type: ignore
        except ImportError as e:
            raise ImportError(
                "ModelCalibrator.fit requires scikit-learn. "
                "Install with: pip install 'sponsio[calibration]'"
            ) from e

        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_scores, [float(lab) for lab in true_labels])
        probe = [i / 100 for i in range(101)]
        calibrated = [float(p) for p in iso.predict(probe)]
        self._maps[model_name] = list(zip(probe, calibrated))
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {m: [list(p) for p in pts] for m, pts in self._maps.items()}
        self._path.write_text(json.dumps(serializable, indent=2))

    @property
    def models(self) -> list[str]:
        return sorted(self._maps)
