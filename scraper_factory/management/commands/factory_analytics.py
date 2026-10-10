"""The six statistic families.

    python manage.py factory_analytics
    python manage.py factory_analytics --family repair
    python manage.py factory_analytics --days 90 --json
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

FAMILIES = ("execution", "repair", "benchmark", "investigation", "strategy",
            "validation")


class Command(BaseCommand):
    help = "Factory analytics: execution, repair, benchmark, investigation, strategy, validation."

    def add_arguments(self, parser):
        parser.add_argument("--family", choices=FAMILIES + ("all",), default="all")
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        from ... import analytics

        days = max(1, options["days"])
        family = options["family"]

        if family == "all":
            data = analytics.all_statistics(days=days)
        else:
            data = {family: getattr(analytics, f"{family}_statistics")(days=days)}

        if options["json"]:
            self.stdout.write(json.dumps(data, indent=2, default=str))
            return

        out = self.stdout.write
        for name, stats in data.items():
            out("")
            out(f"{name.upper()}")
            out("=" * 74)
            self._render(out, stats)

    def _render(self, out, stats, indent: int = 2) -> None:
        pad = " " * indent
        for key, value in stats.items():
            if isinstance(value, list):
                if not value:
                    out(f"{pad}{key:<26} (none)")
                    continue
                out(f"{pad}{key}:")
                for item in value[:10]:
                    if isinstance(item, dict):
                        out(f"{pad}  " + "  ".join(
                            f"{k}={self._fmt(v)}" for k, v in item.items()
                            if k not in ("at",)))
                    else:
                        out(f"{pad}  {item}")
            elif isinstance(value, dict):
                if not value:
                    out(f"{pad}{key:<26} (none)")
                else:
                    out(f"{pad}{key}:")
                    for k, v in list(value.items())[:12]:
                        out(f"{pad}  {str(k):<24} {self._fmt(v)}")
            else:
                out(f"{pad}{key:<26} {self._fmt(value)}")

    @staticmethod
    def _fmt(value):
        if isinstance(value, float):
            # Rates read better as percentages; durations do not.
            return f"{value:.1%}" if 0 <= value <= 1 else f"{value:.2f}"
        return value
