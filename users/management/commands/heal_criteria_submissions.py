from django.core.management.base import BaseCommand
from users.models import Submission, CriteriaItem, CriteriaCategory


class Command(BaseCommand):
    help = 'Heals existing Submission records by mapping stale/historical criteria_id and null category to active CriteriaItem and CriteriaCategory'

    HISTORICAL_CRITERIA_MAP = {
        # Prizes -> From Marian College
        336: "From Marian College",
        258: "From Marian College",
        21: "From Marian College",
        # Prizes -> Outside Marian College
        337: "Outside Marian College",
        259: "Outside Marian College",
        22: "Outside Marian College",
        # Online Courses -> Swayam / NPTEL Course
        318: "Swayam / NPTEL Course",
        199: "Swayam / NPTEL Course",
        3: "Swayam / NPTEL Course",
        # Online Courses -> MOOC Course
        319: "MOOC Course",
        200: "MOOC Course",
        4: "MOOC Course",
        # Academics -> Class Pass Percentage %
        316: "Class Pass Percentage %",
        236: "Class Pass Percentage %",
        1: "Class Pass Percentage %",
        # Academics -> SAVE Sem Result
        317: "SAVE Sem Result",
        237: "SAVE Sem Result",
        2: "SAVE Sem Result",
        # Research -> Publications
        330: "Publications",
        252: "Publications",
        15: "Publications",
        # Career Advancement -> Library - Regular Footfall
        348: "Library - Regular Footfall (Biometric / Entry)",
        270: "Library - Regular Footfall (Biometric / Entry)",
        33: "Library - Regular Footfall (Biometric / Entry)",
        # Career Advancement -> LinkedIn - Profile Completion
        351: "LinkedIn - Profile Completion (Active Profile)",
        36: "LinkedIn - Profile Completion (Active Profile)",
    }

    def handle(self, *args, **options):
        self.stdout.write("Starting submission criteria and category healing...")
        active_items = {item.title.lower().strip(): item for item in CriteriaItem.objects.select_related('category').all()}
        submissions = Submission.objects.all()
        healed_count = 0

        for sub in submissions:
            old_criteria_id = sub.criteria_id
            target_item = CriteriaItem.objects.filter(pk=old_criteria_id).select_related('category').first()

            if not target_item and old_criteria_id in self.HISTORICAL_CRITERIA_MAP:
                title = self.HISTORICAL_CRITERIA_MAP[old_criteria_id]
                target_item = active_items.get(title.lower().strip())

            if not target_item and sub.description:
                desc_lower = sub.description.lower().strip()
                for title, item in active_items.items():
                    if title in desc_lower or desc_lower.startswith(title[:15]):
                        target_item = item
                        break

            if target_item:
                needs_update = False
                update_fields = []
                if sub.criteria_id != target_item.id:
                    sub.criteria_id = target_item.id
                    update_fields.append('criteria_id')
                    needs_update = True
                if target_item.category and sub.category_id != target_item.category_id:
                    sub.category = target_item.category
                    update_fields.append('category')
                    needs_update = True

                if needs_update:
                    sub.save(update_fields=update_fields)
                    healed_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Healed Submission #{sub.id}: criteria_id {old_criteria_id} -> {sub.criteria_id} ('{target_item.title}'), category -> {sub.category.category if sub.category else None}"
                        )
                    )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"Could not resolve criteria for Submission #{sub.id} (criteria_id: {old_criteria_id}, desc: {sub.description[:30]})"
                    )
                )

        self.stdout.write(self.style.SUCCESS(f"Finished healing submissions! Total updated: {healed_count}/{submissions.count()}"))
