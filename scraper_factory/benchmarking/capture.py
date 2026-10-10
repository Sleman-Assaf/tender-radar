"""Proposing benchmark expectations from live investigations.

All 144 seeded cases have blank expectations. `BenchmarkEngine._score` returns
PASS when nothing is declared and the plan is viable, so the suite reports green
while being unable to catch anything — the worst state for a regression net,
because it is indistinguishable from a healthy one.

Filling those in by hand across 144 portals is the reason it never happened, so
this module proposes them from a real investigation. That is a genuinely dangerous
thing to automate: an expectation captured from a broken investigation becomes the
definition of correct, and the suite then defends the bug it recorded. A ratchet
pointed the wrong way is worse than no ratchet.

Three rules follow from that, and they are the whole design:

  1. Nothing is written without `confirm=True`. The default is a proposal.
  2. Only confident, viable investigations are eligible. A portal that could not
     be investigated today yields no expectation — it stays blank and keeps
     scoring UNKNOWN, which is honest.
  3. An expectation a human already declared is never silently changed.
     `overwrite=True` is required, and the previous value is reported.

What this captures is "what the Factory does today, on a portal it clearly
understands". That is a baseline, not ground truth, so every captured case records
its provenance in `notes` — a reviewer can always tell a machine-proposed
expectation from a declared one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field

from django.conf import settings

logger = logging.getLogger("scraper_factory")

#: Below this investigation confidence, nothing is proposed. The default is
#: deliberately high: a marginal read of a portal is exactly the kind of thing
#: that should not become the definition of correct.
MIN_CONFIDENCE = 75


@dataclass
class Proposal:
    """What would be written for one case, and why."""

    case_id: int
    label: str
    url: str

    eligible: bool = False
    reason: str = ""

    data_source: str = ""
    strategy: str = ""
    technology: str = ""
    min_rows: int = 0
    confidence: int = 0
    tier: int = 1
    rows_seen: int = 0

    #: Fields this would change, as name -> (existing, proposed).
    changes: dict = dc_field(default_factory=dict)
    #: Fields skipped because a human already declared them.
    already_declared: dict = dc_field(default_factory=dict)
    written: bool = False

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "label": self.label, "url": self.url,
                "eligible": self.eligible, "reason": self.reason,
                "data_source": self.data_source, "strategy": self.strategy,
                "technology": self.technology, "min_rows": self.min_rows,
                "confidence": self.confidence, "tier": self.tier,
                "rows_seen": self.rows_seen, "changes": self.changes,
                "already_declared": self.already_declared,
                "written": self.written}

    def render(self) -> str:
        # The URL is on the line, not just the label. Several suites hold two
        # cases per portal — the root domain and the actual listing page — whose
        # labels differ only past the point a column would truncate them. In a
        # workflow whose entire purpose is a human saying yes, two rows that look
        # identical are worse than a long line.
        head = f"#{self.case_id} {self.label[:52]}\n      {self.url[:88]}\n      "
        if not self.eligible:
            return head + f"skip — {self.reason}"
        bits = [f"{k.replace('expected_', '')}: {v[0] or '(blank)'} -> {v[1]}"
                for k, v in self.changes.items()]
        if not bits:
            return head + "nothing to add (already fully declared)"
        return head + f"[{self.confidence}% confident] " + "; ".join(bits)


class ExpectationCapturer:
    """Proposes, and on explicit confirmation writes, benchmark expectations."""

    def __init__(self, *, min_confidence: int | None = None,
                 allow_tier2: bool | None = None):
        self.min_confidence = int(
            min_confidence if min_confidence is not None
            else getattr(settings, "FACTORY_BENCHMARK_CAPTURE_MIN_CONFIDENCE",
                         MIN_CONFIDENCE))
        self.allow_tier2 = allow_tier2

    def capture(self, cases, *, confirm: bool = False, overwrite: bool = False,
                include_min_rows: bool = True) -> list[Proposal]:
        """Investigate each case and propose expectations.

        Writes only when `confirm` is true. Returns one Proposal per case either
        way, so a dry run and a real run are directly comparable.
        """
        proposals = []
        for case in cases:
            proposal = self._propose(case, include_min_rows=include_min_rows)
            if proposal.eligible and confirm:
                self._write(case, proposal, overwrite=overwrite)
            proposals.append(proposal)

        written = sum(1 for p in proposals if p.written)
        logger.info("[factory] expectation capture over %s case(s): "
                    "%s eligible, %s written (confirm=%s)",
                    len(proposals), sum(1 for p in proposals if p.eligible),
                    written, confirm)
        return proposals

    # ── proposing ─────────────────────────────────────────────────────────────

    def _propose(self, case, *, include_min_rows: bool) -> Proposal:
        from ..investigation.contracts import Stage

        proposal = Proposal(case_id=case.pk, label=case.label, url=case.url)
        try:
            report, plan = self._investigate(case)
        except Exception as exc:  # noqa: BLE001 — one portal must not stop the sweep
            proposal.reason = f"investigation raised {type(exc).__name__}: {exc}"
            logger.warning("[factory] capture failed for %s: %s", case.url, exc)
            return proposal

        proposal.confidence = report.overall_confidence()
        proposal.tier = report.tier

        rows_stage = report.stage(Stage.CANDIDATE_ROWS)
        best = (rows_stage.data.get("best") or {})
        proposal.rows_seen = int(best.get("count")
                                 or rows_stage.data.get("record_count") or 0)

        # Eligibility. Each refusal leaves the case blank, which scores UNKNOWN —
        # the honest verdict for a portal nobody can currently characterise.
        if not plan.viable:
            proposal.reason = ("the plan is not viable, so today's reading cannot "
                               "define correct behaviour")
            return proposal
        if proposal.confidence < self.min_confidence:
            proposal.reason = (f"confidence {proposal.confidence}% is below the "
                               f"{self.min_confidence}% floor")
            return proposal
        if report.data_source in ("", "UNKNOWN"):
            proposal.reason = "the data source could not be determined"
            return proposal
        root = report.root_cause()
        if root is not None:
            proposal.reason = (f"the investigation still has a root cause "
                               f"({root.stage}/{root.failure})")
            return proposal

        proposal.eligible = True
        proposal.data_source = report.data_source
        proposal.strategy = plan.strategy or ""
        proposal.technology = report.stage(Stage.TECHNOLOGY).conclusion or ""

        if include_min_rows and proposal.rows_seen:
            # A floor, not the observed count: listings gain and lose tenders
            # daily, and an exact count would fail every time the portal is quiet.
            proposal.min_rows = max(1, int(proposal.rows_seen * 0.5))

        self._diff(case, proposal)
        return proposal

    def _investigate(self, case):
        from ..investigation.engine import InvestigationEngine
        from ..planning.planner import Planner

        report = InvestigationEngine().investigate(
            case.url, allow_tier2=self.allow_tier2, capture_snapshot=False)
        return report, Planner().plan(report)

    @staticmethod
    def _diff(case, proposal: Proposal) -> None:
        """Record what would change, keeping declared values separate."""
        fields = {
            "expected_data_source": proposal.data_source,
            "expected_strategy": proposal.strategy,
            "expected_technology": proposal.technology,
        }
        if proposal.min_rows:
            fields["min_rows"] = proposal.min_rows

        for name, proposed in fields.items():
            if not proposed:
                continue
            existing = getattr(case, name)
            blank = existing in ("", 0, None)
            if blank:
                proposal.changes[name] = ("", proposed)
            elif existing != proposed:
                # A human said something different. Their value stands unless
                # overwrite is asked for, and the disagreement is surfaced either
                # way — it may be the Factory that is wrong.
                proposal.already_declared[name] = (existing, proposed)

    # ── writing ───────────────────────────────────────────────────────────────

    def _write(self, case, proposal: Proposal, *, overwrite: bool) -> None:
        updates = dict(proposal.changes)
        if overwrite:
            updates.update(proposal.already_declared)
            proposal.changes.update(proposal.already_declared)
            proposal.already_declared = {}
        if not updates:
            return

        for name, (_old, new) in updates.items():
            setattr(case, name, new)

        note = (f"expectations captured from a tier-{proposal.tier} investigation "
                f"at {proposal.confidence}% confidence")
        case.notes = (note if not case.notes or "captured from" in case.notes
                      else f"{case.notes[:200]} | {note}")[:300]
        case.save(update_fields=list(updates) + ["notes"])
        proposal.written = True
        logger.info("[factory] captured expectations for case #%s (%s): %s",
                    case.pk, case.label, ", ".join(updates))


def summarise(proposals: list[Proposal]) -> dict:
    """Counts a reviewer needs before confirming."""
    eligible = [p for p in proposals if p.eligible]
    return {
        "cases": len(proposals),
        "eligible": len(eligible),
        "skipped": len(proposals) - len(eligible),
        "would_change": sum(1 for p in eligible if p.changes),
        "already_declared": sum(1 for p in proposals if p.already_declared),
        "written": sum(1 for p in proposals if p.written),
        "skip_reasons": _tally(p.reason for p in proposals if not p.eligible),
    }


def _tally(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        # Group by the shape of the reason, not its numbers, so twenty portals
        # below the confidence floor read as one line.
        key = value.split(",")[0][:60]
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
