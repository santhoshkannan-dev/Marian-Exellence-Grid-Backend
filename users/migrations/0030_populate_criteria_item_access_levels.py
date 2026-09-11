"""
Migration 0030 — Populate CriteriaItem.access_level based on the authoritative
subcategory permission matrix defined in users/access_rules.py.

Changes made:
  - Cat 1, 9, 10, 11 items → access_level = 'dqc_only'  (inherited from category)
  - Cat 8 group-prize items → access_level = 'dqc_only'
  - Cat 8 individual/participation items → access_level = 'all_students'
  - Cat 12 library + repository items → access_level = 'dqc_only'
  - Cat 12 LinkedIn items → access_level = 'all_students'
  - All other items → access_level = 'all_students'

Decision log (2026-09-11):
  - Participation (Individual) prizes → all_students  (confirmed by user)
  - LinkedIn Skill Badges (1104) and Micro-credentials (1105) → all_students, excluded from DQC scope
  - Programs Organised (Cat 9) → dqc_only items, no cycle limit
"""

from django.db import migrations


# Helpers — must NOT import from users.access_rules at module level
# (migrations must be self-contained to avoid import-time side effects).

def _norm(text):
    return str(text or '').strip().lower()


def _expected_access_level(cat_code, item_title):
    """Return expected access_level for a CriteriaItem given its category code and title."""
    # DQC-only category codes (whole-category restriction)
    DQC_ONLY = {
        'cat-academics',
        'cat-programs-organized',
        'cat-leadership',
        'cat-leaderships',
        'cat-social-responsibility',
        'cat-social-responsibilities',
        'cat-documentation',
    }
    if cat_code in DQC_ONLY:
        return 'dqc_only'

    # Cat 8 — Prizes: group subcategories are DQC-only
    if cat_code == 'cat-prizes':
        if '(group)' in item_title and 'participation (individual)' not in item_title:
            return 'dqc_only'
        if 'participation (group)' in item_title:
            return 'dqc_only'
        return 'all_students'

    # Cat 12 — Career Advancement: library + repository are DQC-only
    if cat_code == 'cat-career-advancement':
        DQC_CAREER_TITLES = {
            'library - regular footfall (biometric / entry)',
            'library - academic & career books issued/read',
            'library - academic and career books issued/read',
            'repository creation (drive / github / lms / website)',
        }
        for dqc_title in DQC_CAREER_TITLES:
            if dqc_title in item_title:
                return 'dqc_only'
        # Keyword fallbacks
        for keyword in ('library', 'footfall', 'repository'):
            if keyword in item_title:
                return 'dqc_only'
        return 'all_students'

    return 'all_students'


def populate_access_levels(apps, schema_editor):
    CriteriaItem = apps.get_model('users', 'CriteriaItem')
    CriteriaCategory = apps.get_model('users', 'CriteriaCategory')

    updated = 0
    for item in CriteriaItem.objects.select_related('category').all():
        cat_code = _norm(item.category.code if item.category else '')
        item_title = _norm(item.title)
        expected = _expected_access_level(cat_code, item_title)

        if item.access_level != expected:
            item.access_level = expected
            item.save(update_fields=['access_level'])
            updated += 1

    # Also ensure CriteriaCategory.access_level is consistent at the category level
    DQC_CATEGORY_CODES = {
        'cat-academics', 'cat-programs-organized',
        'cat-leadership', 'cat-leaderships',
        'cat-social-responsibility', 'cat-social-responsibilities',
        'cat-documentation',
    }
    for cat in CriteriaCategory.objects.all():
        code = _norm(cat.code)
        expected_cat = 'dqc_only' if code in DQC_CATEGORY_CODES else 'all_students'
        # Hybrid categories keep their current setting — only update if it's clearly wrong
        if code in ('cat-prizes', 'cat-career-advancement'):
            continue
        if cat.access_level != expected_cat:
            cat.access_level = expected_cat
            cat.save(update_fields=['access_level'])

    print(f"[0030] Updated access_level on {updated} CriteriaItem rows.")


def reverse_access_levels(apps, schema_editor):
    """
    Reversal sets everything back to 'all_students'.
    This is intentionally conservative — the previous state is not stored.
    """
    CriteriaItem = apps.get_model('users', 'CriteriaItem')
    CriteriaItem.objects.all().update(access_level='all_students')


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0029_alter_verificationlog_options_and_more'),
    ]

    operations = [
        migrations.RunPython(
            populate_access_levels,
            reverse_code=reverse_access_levels,
        ),
    ]
