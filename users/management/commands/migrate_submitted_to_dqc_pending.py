"""
Management command: migrate_submitted_to_dqc_pending
=====================================================
Auto-migrates all legacy Submission records in 'Submitted' state to 'DQC_PENDING'
to ensure pipeline consistency under the forward-only 3-tier verification architecture.

Usage:
    python manage.py migrate_submitted_to_dqc_pending
    python manage.py migrate_submitted_to_dqc_pending --dry-run
    python manage.py migrate_submitted_to_dqc_pending --academic-year 2025-2026
"""

import logging
from django.core.management.base import BaseCommand
from django.db import transaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Migrates legacy 'Submitted' submissions to 'DQC_PENDING' for pipeline consistency. "
        "Under the forward-only verification architecture, 'Submitted' is the student self-save "
        "state. 'DQC_PENDING' is the canonical Round 1 intake state."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview the records that would be migrated without making any changes.',
        )
        parser.add_argument(
            '--academic-year',
            type=str,
            default=None,
            help='Restrict migration to a specific academic year (e.g. 2025-2026). Defaults to all years.',
        )

    def handle(self, *args, **options):
        from users.models import Submission

        dry_run = options['dry_run']
        academic_year = options.get('academic_year')

        qs = Submission.objects.filter(status='Submitted')
        if academic_year:
            qs = qs.filter(academic_year=academic_year)

        total = qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No 'Submitted' submissions found. Nothing to migrate."))
            return

        self.stdout.write(
            f"Found {total} submission(s) in 'Submitted' state"
            + (f" for academic year {academic_year}" if academic_year else " (all years)")
            + "."
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] The following submissions would be migrated:"))
            for sub in qs.select_related('user', 'user__class_name')[:100]:
                class_name = sub.user.class_name.name if sub.user and sub.user.class_name else 'N/A'
                self.stdout.write(
                    f"  ID={sub.id} | User={sub.user.email if sub.user else 'N/A'} | "
                    f"Class={class_name} | AY={sub.academic_year}"
                )
            if total > 100:
                self.stdout.write(f"  ... and {total - 100} more.")
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes made."))
            return

        # Perform atomic bulk update
        try:
            with transaction.atomic():
                updated_count = qs.update(status='DQC_PENDING')
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Migration failed: {e}"))
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully migrated {updated_count} submission(s) from 'Submitted' → 'DQC_PENDING'."
            )
        )
        logger.info(
            f"migrate_submitted_to_dqc_pending: migrated {updated_count} submissions to DQC_PENDING"
            + (f" (academic_year={academic_year})" if academic_year else "")
        )
