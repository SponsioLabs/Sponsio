"""``sponsio eval`` — batch trace replay with confusion-matrix metrics.

The point of this command is to answer the only question that
matters before flipping ``SPONSIO_MODE=enforce``:

    "If I turn enforcement on tomorrow, how many *real* incidents do
    my contracts catch, and how much *legit* traffic do they kill?"

Mechanically: replay a labelled corpus of traces against the
configured contracts and emit a per-contract confusion matrix plus
the four headline rates (precision, recall, FPR, FNR).

Labels are read from the filename — files prefixed ``safe_`` are
expected to pass every contract, files prefixed ``unsafe_`` are
expected to be blocked by *at least one* contract.  This convention
is deliberately simple so users can build a corpus by dropping
files into a folder, no schema or front-matter required.

Confusion matrix definitions (per contract):

    TP — predicted block, actually unsafe   (correct catch)
    FP — predicted block, actually safe     (overblock — costs trust)
    FN — predicted allow, actually unsafe   (miss — costs safety)
    TN — predicted allow, actually safe     (correct allow)

For the overall corpus, a trace is "blocked" if *any* contract
violates on it — so a single trigger-happy contract can poison the
whole agent's overblock rate, which is exactly the failure mode
``eval`` is designed to make visible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

Label = Literal["safe", "unsafe", "unknown"]


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    """One labelled trace file ready for replay."""

    path: Path
    label: Label
    trace: Any  # sponsio.models.trace.Trace — loaded lazily

    @property
    def name(self) -> str:
        return self.path.name


def _label_from_filename(name: str) -> Label:
    """Parse the ``safe_`` / ``unsafe_`` filename prefix.

    Conservative: anything that doesn't match one of the two
    canonical prefixes returns ``"unknown"`` so the eval runner can
    skip it (vs silently mislabelling it as ``safe`` and inflating
    the FPR).
    """
    lower = name.lower()
    if lower.startswith("unsafe_") or lower.startswith("unsafe-"):
        return "unsafe"
    if lower.startswith("safe_") or lower.startswith("safe-"):
        return "safe"
    return "unknown"


def discover_cases(path: Path) -> list[EvalCase]:
    """Walk ``path`` and load every ``*.json`` trace file.

    Accepts either a single file or a directory.  Directory walk is
    NOT recursive — eval corpora are usually shallow and we want
    "dump files into a folder" to be the obvious workflow without
    accidentally sweeping in unrelated ``.json`` from
    ``node_modules/`` and the like.
    """
    from sponsio.tracer.otel_consumer import otel_to_trace

    files: list[Path]
    if path.is_file():
        files = [path]
    else:
        files = sorted(p for p in path.glob("*.json") if p.is_file())

    cases: list[EvalCase] = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            trace = otel_to_trace(data)
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError, TypeError):
            # Skip malformed files rather than abort — the corpus
            # might contain notes or work-in-progress.  ``otel_to_trace``
            # is permissive about shape and can raise a variety of
            # non-Json errors when given the wrong root type (e.g. a
            # list instead of a dict), so we cast a broad-but-bounded
            # net here.
            continue
        cases.append(EvalCase(path=f, label=_label_from_filename(f.name), trace=trace))
    return cases


# ---------------------------------------------------------------------------
# Per-contract result + aggregation
# ---------------------------------------------------------------------------


@dataclass
class CaseOutcome:
    """One (contract × case) verification result."""

    case_name: str
    contract_nl: str
    label: Label
    blocked: bool  # contract said violation
    skipped: bool = False  # sto contract or unparseable — not counted

    @property
    def is_tp(self) -> bool:
        return not self.skipped and self.blocked and self.label == "unsafe"

    @property
    def is_fp(self) -> bool:
        return not self.skipped and self.blocked and self.label == "safe"

    @property
    def is_fn(self) -> bool:
        return not self.skipped and not self.blocked and self.label == "unsafe"

    @property
    def is_tn(self) -> bool:
        return not self.skipped and not self.blocked and self.label == "safe"


@dataclass
class ContractMetrics:
    """Confusion matrix + derived rates for a single contract."""

    contract_nl: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    skipped: int = 0

    @property
    def total_labelled(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def precision(self) -> float | None:
        denom = self.tp + self.fp
        return self.tp / denom if denom else None

    @property
    def recall(self) -> float | None:
        denom = self.tp + self.fn
        return self.tp / denom if denom else None

    @property
    def fpr(self) -> float | None:
        """Overblock rate among legitimate traffic."""
        denom = self.fp + self.tn
        return self.fp / denom if denom else None

    @property
    def fnr(self) -> float | None:
        """Miss rate among real incidents."""
        denom = self.fn + self.tp
        return self.fn / denom if denom else None


@dataclass
class EvalReport:
    """Full per-contract + overall report."""

    contracts: list[ContractMetrics] = field(default_factory=list)
    n_cases: int = 0
    n_safe: int = 0
    n_unsafe: int = 0
    n_unlabelled: int = 0

    # Overall (any contract blocks → blocked)
    overall_tp: int = 0
    overall_fp: int = 0
    overall_fn: int = 0
    overall_tn: int = 0

    @property
    def overall_fpr(self) -> float | None:
        denom = self.overall_fp + self.overall_tn
        return self.overall_fp / denom if denom else None

    @property
    def overall_fnr(self) -> float | None:
        denom = self.overall_fn + self.overall_tp
        return self.overall_fn / denom if denom else None

    def to_dict(self) -> dict:
        return {
            "n_cases": self.n_cases,
            "n_safe": self.n_safe,
            "n_unsafe": self.n_unsafe,
            "n_unlabelled": self.n_unlabelled,
            "overall": {
                "tp": self.overall_tp,
                "fp": self.overall_fp,
                "fn": self.overall_fn,
                "tn": self.overall_tn,
                "fpr": self.overall_fpr,
                "fnr": self.overall_fnr,
            },
            "contracts": [
                {
                    "nl": m.contract_nl,
                    "tp": m.tp,
                    "fp": m.fp,
                    "fn": m.fn,
                    "tn": m.tn,
                    "skipped": m.skipped,
                    "precision": m.precision,
                    "recall": m.recall,
                    "fpr": m.fpr,
                    "fnr": m.fnr,
                }
                for m in self.contracts
            ],
        }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _eval_contract_on_trace(parsed: Any, trace: Any) -> bool | None:
    """Returns True if the contract is *violated* on the trace,
    False if satisfied, or ``None`` if it can't be evaluated
    deterministically (sto contract, unparseable, etc).
    """
    if parsed is None or not getattr(parsed, "is_det", False):
        return None
    from sponsio.formulas.evaluator import evaluate as eval_formula
    from sponsio.tracer.grounding import ground

    valuations = ground(trace)
    holds = eval_formula(parsed.hard.formula, valuations)
    return not holds


def run_eval(cases: Iterable[EvalCase], contracts: list[str]) -> EvalReport:
    """Replay ``cases`` against each NL contract and tally a report.

    Skipped contracts (sto / unparseable) appear in the per-contract
    section with ``skipped > 0`` and zero TP/FP/FN/TN — they're
    visible but excluded from rate calculations because we don't
    deterministically know their predicted outcome.
    """
    from sponsio.cli import _resolve_entry

    report = EvalReport()
    cases = list(cases)
    report.n_cases = len(cases)
    report.n_safe = sum(1 for c in cases if c.label == "safe")
    report.n_unsafe = sum(1 for c in cases if c.label == "unsafe")
    report.n_unlabelled = sum(1 for c in cases if c.label == "unknown")

    # Pre-parse contracts once
    parsed_contracts: list[tuple[str, Any]] = []
    for nl in contracts:
        nl_text, parsed = _resolve_entry(nl)
        parsed_contracts.append((nl_text, parsed))
        report.contracts.append(ContractMetrics(contract_nl=nl_text))

    # Per-case outer loop so we can compute "any contract blocked → blocked"
    # for the overall confusion matrix.
    for case in cases:
        any_blocked = False
        for (nl_text, parsed), metric in zip(parsed_contracts, report.contracts):
            verdict = _eval_contract_on_trace(parsed, case.trace)
            if verdict is None:
                metric.skipped += 1
                continue
            blocked = bool(verdict)
            any_blocked = any_blocked or blocked
            outcome = CaseOutcome(
                case_name=case.name,
                contract_nl=nl_text,
                label=case.label,
                blocked=blocked,
            )
            if outcome.is_tp:
                metric.tp += 1
            elif outcome.is_fp:
                metric.fp += 1
            elif outcome.is_fn:
                metric.fn += 1
            elif outcome.is_tn:
                metric.tn += 1
            # ``label == "unknown"`` falls through — counted only in
            # ``n_unlabelled``, never in confusion matrix.

        if case.label == "unsafe":
            if any_blocked:
                report.overall_tp += 1
            else:
                report.overall_fn += 1
        elif case.label == "safe":
            if any_blocked:
                report.overall_fp += 1
            else:
                report.overall_tn += 1

    return report


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Baseline diff (CI regression gate)
# ---------------------------------------------------------------------------


@dataclass
class ContractDiff:
    """Delta for one contract between baseline and current report.

    A contract may appear in only one of the two reports (added or
    removed in the PR under test).  The flags let the renderer
    surface that explicitly instead of pretending a None→0.05 jump
    is a regression.
    """

    contract_nl: str
    in_baseline: bool
    in_current: bool
    fpr_before: float | None = None
    fpr_after: float | None = None
    fnr_before: float | None = None
    fnr_after: float | None = None

    @property
    def fpr_delta(self) -> float | None:
        if self.fpr_before is None or self.fpr_after is None:
            return None
        return self.fpr_after - self.fpr_before

    @property
    def fnr_delta(self) -> float | None:
        if self.fnr_before is None or self.fnr_after is None:
            return None
        return self.fnr_after - self.fnr_before


@dataclass
class BaselineDiff:
    """Full diff between two ``EvalReport``s.

    Designed to drive a CI gate: ``--max-fpr-delta`` /
    ``--max-fnr-delta`` translate the overall deltas into a non-zero
    exit code so a PR that regresses safety or overblock rates can
    fail the build *automatically*, not "if a reviewer remembers to
    check the eval output".
    """

    contracts: list[ContractDiff] = field(default_factory=list)
    n_cases_before: int = 0
    n_cases_after: int = 0
    overall_fpr_before: float | None = None
    overall_fpr_after: float | None = None
    overall_fnr_before: float | None = None
    overall_fnr_after: float | None = None

    @property
    def overall_fpr_delta(self) -> float | None:
        if self.overall_fpr_before is None or self.overall_fpr_after is None:
            return None
        return self.overall_fpr_after - self.overall_fpr_before

    @property
    def overall_fnr_delta(self) -> float | None:
        if self.overall_fnr_before is None or self.overall_fnr_after is None:
            return None
        return self.overall_fnr_after - self.overall_fnr_before

    def gate_violations(
        self,
        *,
        max_fpr_delta: float | None = None,
        max_fnr_delta: float | None = None,
    ) -> list[str]:
        """Return human-readable violation messages for failed gates.

        Empty list = all gates passed (or no gates configured).  An
        unset (None) overall rate after applying the gate is treated
        as "no signal" rather than a failure — a baseline-but-no-
        current corpus is operator error, not a regression.
        """
        out: list[str] = []
        if max_fpr_delta is not None:
            d = self.overall_fpr_delta
            if d is not None and d > max_fpr_delta:
                out.append(
                    f"overall FPR rose by {d * 100:.2f}pp "
                    f"(gate: --max-fpr-delta {max_fpr_delta * 100:.2f}pp)"
                )
        if max_fnr_delta is not None:
            d = self.overall_fnr_delta
            if d is not None and d > max_fnr_delta:
                out.append(
                    f"overall FNR rose by {d * 100:.2f}pp "
                    f"(gate: --max-fnr-delta {max_fnr_delta * 100:.2f}pp)"
                )
        return out

    def to_dict(self) -> dict:
        return {
            "n_cases_before": self.n_cases_before,
            "n_cases_after": self.n_cases_after,
            "overall": {
                "fpr_before": self.overall_fpr_before,
                "fpr_after": self.overall_fpr_after,
                "fpr_delta": self.overall_fpr_delta,
                "fnr_before": self.overall_fnr_before,
                "fnr_after": self.overall_fnr_after,
                "fnr_delta": self.overall_fnr_delta,
            },
            "contracts": [
                {
                    "nl": c.contract_nl,
                    "status": (
                        "added"
                        if not c.in_baseline
                        else "removed"
                        if not c.in_current
                        else "changed"
                    ),
                    "fpr_before": c.fpr_before,
                    "fpr_after": c.fpr_after,
                    "fpr_delta": c.fpr_delta,
                    "fnr_before": c.fnr_before,
                    "fnr_after": c.fnr_after,
                    "fnr_delta": c.fnr_delta,
                }
                for c in self.contracts
            ],
        }


def diff_reports(baseline: dict, current: EvalReport) -> BaselineDiff:
    """Compute a ``BaselineDiff`` from a baseline JSON dict + current report.

    ``baseline`` is the dict produced by ``EvalReport.to_dict()`` and
    saved on disk — we accept the dict directly (rather than reading
    the file ourselves) so this function is testable without
    touching the filesystem and so callers can stitch in custom
    storage backends (S3, dashboard API, …) trivially.

    Contracts are matched by their ``nl`` string.  Two contracts
    with the same NL but different parsed semantics (e.g. an
    operator edited a tool name in YAML) will *look* like a single
    "changed" entry — that's a known limitation; for now the
    workaround is "contract NL strings are the identity, treat them
    like database keys".
    """
    diff = BaselineDiff(
        n_cases_before=int(baseline.get("n_cases", 0)),
        n_cases_after=current.n_cases,
        overall_fpr_before=baseline.get("overall", {}).get("fpr"),
        overall_fpr_after=current.overall_fpr,
        overall_fnr_before=baseline.get("overall", {}).get("fnr"),
        overall_fnr_after=current.overall_fnr,
    )

    base_by_nl: dict[str, dict] = {c["nl"]: c for c in baseline.get("contracts", [])}
    cur_by_nl: dict[str, ContractMetrics] = {
        m.contract_nl: m for m in current.contracts
    }

    # Walk the union, in current-then-baseline order so the renderer
    # shows new contracts first (most likely the focus of the PR).
    for nl, m in cur_by_nl.items():
        b = base_by_nl.get(nl)
        diff.contracts.append(
            ContractDiff(
                contract_nl=nl,
                in_baseline=b is not None,
                in_current=True,
                fpr_before=(b or {}).get("fpr"),
                fpr_after=m.fpr,
                fnr_before=(b or {}).get("fnr"),
                fnr_after=m.fnr,
            )
        )
    for nl, b in base_by_nl.items():
        if nl in cur_by_nl:
            continue
        diff.contracts.append(
            ContractDiff(
                contract_nl=nl,
                in_baseline=True,
                in_current=False,
                fpr_before=b.get("fpr"),
                fnr_before=b.get("fnr"),
            )
        )

    return diff


def _fmt_delta(d: float | None) -> str:
    """Render a delta as ``+1.5pp`` / ``-0.3pp`` / ``—``.

    Percentage points (not relative %) because that's how operators
    actually reason about FPR: "went from 2% to 4% = +2pp" is the
    intuitive read; "+100%" of a tiny base is misleading.
    """
    if d is None:
        return "    —"
    sign = "+" if d >= 0 else "−"
    return f"{sign}{abs(d) * 100:4.2f}pp"


def format_diff(diff: BaselineDiff) -> str:
    """Human-readable rendering of a ``BaselineDiff``."""
    lines: list[str] = ["", "Baseline diff:"]
    lines.append(f"  cases: {diff.n_cases_before} → {diff.n_cases_after}")
    lines.append(
        f"  overall FPR: {_fmt_rate(diff.overall_fpr_before)} → "
        f"{_fmt_rate(diff.overall_fpr_after)}  "
        f"({_fmt_delta(diff.overall_fpr_delta)})"
    )
    lines.append(
        f"  overall FNR: {_fmt_rate(diff.overall_fnr_before)} → "
        f"{_fmt_rate(diff.overall_fnr_after)}  "
        f"({_fmt_delta(diff.overall_fnr_delta)})"
    )

    if diff.contracts:
        lines.append("")
        lines.append("Per contract:")
        header = f"  {'status':<8} {'FPR Δ':>7} {'FNR Δ':>7}  contract"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for c in diff.contracts:
            status = (
                "added"
                if not c.in_baseline
                else "removed"
                if not c.in_current
                else "changed"
            )
            nl = (
                c.contract_nl
                if len(c.contract_nl) <= 50
                else c.contract_nl[:47] + "..."
            )
            lines.append(
                f"  {status:<8} {_fmt_delta(c.fpr_delta):>7} "
                f"{_fmt_delta(c.fnr_delta):>7}  {nl}"
            )
    lines.append("")
    return "\n".join(lines)


def _fmt_rate(r: float | None) -> str:
    return "—" if r is None else f"{r * 100:5.1f}%"


def format_report(report: EvalReport) -> str:
    """Human-readable rendering used by the CLI when ``--json`` is off."""
    lines: list[str] = []
    lines.append("")
    lines.append(
        f"Eval — {report.n_cases} cases ({report.n_safe} safe, "
        f"{report.n_unsafe} unsafe, {report.n_unlabelled} unlabelled)"
    )
    lines.append("")

    # Per-contract table
    if report.contracts:
        lines.append("Per contract:")
        header = f"  {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4}  {'FPR':>6} {'FNR':>6}  {'skip':>4}  contract"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for m in report.contracts:
            nl = (
                m.contract_nl
                if len(m.contract_nl) <= 60
                else m.contract_nl[:57] + "..."
            )
            lines.append(
                f"  {m.tp:>4} {m.fp:>4} {m.fn:>4} {m.tn:>4}  "
                f"{_fmt_rate(m.fpr):>6} {_fmt_rate(m.fnr):>6}  "
                f"{m.skipped:>4}  {nl}"
            )
        lines.append("")

    # Overall
    if report.n_safe + report.n_unsafe > 0:
        lines.append("Overall (any contract blocks → blocked):")
        lines.append(
            f"  TP={report.overall_tp}  FP={report.overall_fp}  "
            f"FN={report.overall_fn}  TN={report.overall_tn}"
        )
        lines.append(f"  FPR (overblock):  {_fmt_rate(report.overall_fpr)}")
        lines.append(f"  FNR (miss):       {_fmt_rate(report.overall_fnr)}")
    else:
        lines.append(
            "No labelled cases — name files ``safe_*.json`` / "
            "``unsafe_*.json`` to enable confusion-matrix metrics."
        )
    lines.append("")
    return "\n".join(lines)
