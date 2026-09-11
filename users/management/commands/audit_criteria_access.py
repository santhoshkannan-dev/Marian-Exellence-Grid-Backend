"""
management/commands/audit_criteria_access.py

Usage:
    python manage.py audit_criteria_access           # Print audit table only
    python manage.py audit_criteria_access --fix     # Apply corrections to DB
    python manage.py audit_criteria_access --fix --verbose  # Fix + full row dump

Prints a table of every CriteriaItem with:
  ID | Category Code | Title | Current access_level | Expected access_level | Status
"""

from django.core.management.base import BaseCommand
from users.access_rules import get_expected_access_level_for_item


class Command(BaseCommand):
    help = (
        "Audit CriteriaItem.access_level values against the authoritative "
        "access-control matrix in users/access_rules.py. "
        "Use --fix to apply corrections."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            default=False,
            help='Apply corrections to database rows that have wrong access_level.',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            default=False,
            help='Print all rows (not just mismatches).',
        )
        parser.add_argument(
            '--category',
            type=str,
            default=None,
            help='Restrict audit to a single category code (e.g. cat-prizes).',
        )

    def handle(self, *args, **options):
        from users.models import CriteriaItem

        fix = options['fix']
        verbose = options['verbose']
        cat_filter = options.get('category')

        qs = CriteriaItem.objects.select_related('category').order_by(
            'category__code', 'id'
        )
        if cat_filter:
            qs = qs.filter(category__code__iexact=cat_filter)

        total = qs.count()
        mismatches = 0
        fixed = 0

        # Header
        self.stdout.write(
            self.style.HTTP_INFO(
                f"\n{'ID':>6} | {'Cat Code':<28} | {'Item Title':<55} | "
                f"{'Current':>12} | {'Expected':>12} | Status"
            )
        )
        self.stdout.write('-' * 140)

        for item in qs:
            cat_code = (item.category.code or '') if item.category else ''
            expected = get_expected_access_level_for_item(item)
            current = item.access_level or 'all_students'
            match = (current == expected)

            if match and not verbose:
                continue

            status_label = (
                self.style.SUCCESS('[OK]')
                if match
                else self.style.ERROR('[MISMATCH]')
            )

            title_display = (item.title or '')[:54]
            self.stdout.write(
                f"{item.id:>6} | {cat_code:<28} | {title_display:<55} | "
                f"{current:>12} | {expected:>12} | {status_label}"
            )

            if not match:
                mismatches += 1
                if fix:
                    item.access_level = expected
                    item.save(update_fields=['access_level'])
                    fixed += 1

        # Summary
        self.stdout.write('-' * 140)
        self.stdout.write(
            f"\nAudit complete — {total} items checked, "
            f"{mismatches} mismatch(es) found."
        )
        if fix:
            self.stdout.write(
                self.style.SUCCESS(f"Applied corrections to {fixed} row(s).")
            )
        elif mismatches > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Run with --fix to apply corrections to {mismatches} row(s)."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("All access levels are correct."))
