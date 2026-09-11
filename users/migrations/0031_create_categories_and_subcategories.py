"""
Migration 0031 — Create categories and subcategories lookup tables.

Enforces subcategory-based dynamic mark calculations where calculated marks are strictly
driven by subcategories.default_marks.
"""

from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion


CATEGORIES_DATA = [
    (1, 'cat-academics', 'Academics'),
    (2, 'cat-online-courses', 'Online Courses'),
    (3, 'cat-competitive-exams', 'Competitive Exams'),
    (4, 'cat-internships', 'Internships'),
    (5, 'cat-scholarships', 'Scholarships'),
    (6, 'cat-research', 'Research'),
    (7, 'cat-startups', 'Startups'),
    (8, 'cat-prizes', 'Prizes Won'),
    (9, 'cat-programs-organized', 'Programs Organized'),
    (10, 'cat-leadership', 'Leaderships'),
    (11, 'cat-social-responsibility', 'Social Responsibilities'),
    (12, 'cat-career-advancement', 'Career Advancement'),
]

# (category_id, subcategory_name, default_marks, requires_dqc, max_per_cycle)
SUBCATEGORIES_DATA = [
    # Category 2: Online Courses
    (2, 'Swayam / NPTEL Course', Decimal('5.00'), False, 3),
    (2, 'MOOC Course', Decimal('2.00'), False, 3),

    # Category 3: Competitive Exams
    (3, 'JRF Passed', Decimal('20.00'), False, 1),
    (3, 'NET Passed', Decimal('10.00'), False, 1),
    (3, 'Any Other Relevant Exam (IELTS, Language, etc.)', Decimal('3.00'), False, 2),
    (3, 'Any Other Relevant Exam (IELTS, PET, Language Specific, etc.)', Decimal('3.00'), False, 2),
    (3, 'Participation in Relevant Exam (UPSC / PSC)', Decimal('1.00'), False, 3),
    (3, 'Participation in Relevant Exam (UPSC / PSC Exams)', Decimal('1.00'), False, 3),

    # Category 4: Internships
    (4, 'Offline Internship (Min. 1 month)', Decimal('5.00'), False, 2),
    (4, 'Online Internship (Min. 1 month)', Decimal('3.00'), False, 2),

    # Category 5: Scholarships
    (5, 'International Level Scholarship', Decimal('20.00'), False, 1),
    (5, 'National Level Scholarship', Decimal('10.00'), False, 1),
    (5, 'State Level Scholarship', Decimal('5.00'), False, 1),
    (5, 'District Level Scholarship', Decimal('2.00'), False, 1),

    # Category 6: Research
    (6, 'Scopus / Web of Science', Decimal('10.00'), False, 3),
    (6, 'Conference Proceeding / Peer reviewed article', Decimal('5.00'), False, 3),
    (6, 'Paper Presentation - Outside Marian College', Decimal('5.00'), False, 3),
    (6, 'Paper Presentation - Inside Marian College', Decimal('3.00'), False, 3),
    (6, 'Paper Presentation', Decimal('5.00'), False, 3),
    (6, 'Patents - Utility', Decimal('10.00'), False, 2),
    (6, 'Patents - Design', Decimal('5.00'), False, 2),
    (6, 'Patents', Decimal('10.00'), False, 2),
    (6, 'Book Publications - Book', Decimal('10.00'), False, 2),
    (6, 'Book Publications - Book Chapter', Decimal('5.00'), False, 2),
    (6, 'Book Publications - Article', Decimal('2.00'), False, 2),
    (6, 'Book Publications', Decimal('10.00'), False, 2),
    (6, 'Funded Projects - International', Decimal('20.00'), False, 1),
    (6, 'Funded Projects - National', Decimal('10.00'), False, 1),
    (6, 'Funded Projects - State', Decimal('5.00'), False, 1),
    (6, 'Funded Projects - Any Other', Decimal('3.00'), False, 1),
    (6, 'Funded Projects', Decimal('10.00'), False, 1),
    (6, 'Publications', Decimal('10.00'), False, 3),

    # Category 7: Startups
    (7, 'Government-Registered Start-up', Decimal('10.00'), False, 1),

    # Category 8: Prizes Won
    (8, '1st Prize (Individual) - Outside', Decimal('15.00'), False, 3),
    (8, '2nd Prize (Individual) - Outside', Decimal('10.00'), False, 3),
    (8, '3rd Prize (Individual) - Outside', Decimal('5.00'), False, 3),
    (8, 'Participation (Individual) - Outside', Decimal('3.00'), False, 3),
    (8, '1st Prize (Group) - Outside', Decimal('10.00'), True, 3),
    (8, '2nd Prize (Group) - Outside', Decimal('5.00'), True, 3),
    (8, '3rd Prize (Group) - Outside', Decimal('3.00'), True, 3),
    (8, 'Participation (Group) - Outside', Decimal('2.00'), True, 3),
    (8, '1st Prize (Individual) - Marian', Decimal('10.00'), False, 3),
    (8, '2nd Prize (Individual) - Marian', Decimal('5.00'), False, 3),
    (8, '3rd Prize (Individual) - Marian', Decimal('3.00'), False, 3),
    (8, '1st Prize (Group) - Marian', Decimal('5.00'), True, 3),
    (8, '2nd Prize (Group) - Marian', Decimal('3.00'), True, 3),
    (8, '3rd Prize (Group) - Marian', Decimal('2.00'), True, 3),
    (8, '1st Prize (Individual)', Decimal('10.00'), False, 3),
    (8, '2nd Prize (Individual)', Decimal('5.00'), False, 3),
    (8, '3rd Prize (Individual)', Decimal('3.00'), False, 3),
    (8, '1st Prize (Group)', Decimal('5.00'), True, 3),
    (8, '2nd Prize (Group)', Decimal('3.00'), True, 3),
    (8, '3rd Prize (Group)', Decimal('2.00'), True, 3),
    (8, 'Participation (Individual)', Decimal('3.00'), False, 3),
    (8, 'Participation (Group)', Decimal('2.00'), True, 3),

    # Category 9: Programs Organized
    (9, 'Intercollegiate', Decimal('5.00'), True, None),
    (9, 'Intra - Collegiate', Decimal('3.00'), True, None),
    (9, 'Intracollegiate', Decimal('3.00'), True, None),
    (9, 'Class Magazine', Decimal('5.00'), True, None),

    # Category 10: Leaderships
    (10, 'MCSC Executive Body Position', Decimal('5.00'), True, 2),
    (10, 'SAHYA Executive Body Position', Decimal('5.00'), True, 2),
    (10, 'Clubs & Associations Leadership Position', Decimal('5.00'), True, 2),
    (10, 'Innovative / Sustainable Suggestion', Decimal('5.00'), True, 2),

    # Category 11: Social Responsibilities
    (11, 'Coordination of Event (Community Action / Outreach)', Decimal('5.00'), True, 3),
    (11, 'Participation in Event', Decimal('3.00'), True, 3),
    (11, 'News Media Coverage (Excluding Social Media)', Decimal('3.00'), True, 3),
]


def seed_categories_and_subcategories(apps, schema_editor):
    Category = apps.get_model('users', 'Category')
    SubCategory = apps.get_model('users', 'SubCategory')
    Submission = apps.get_model('users', 'Submission')
    CriteriaItem = apps.get_model('users', 'CriteriaItem')

    for cid, code, name in CATEGORIES_DATA:
        Category.objects.update_or_create(
            id=cid,
            defaults={'code': code, 'name': name}
        )

    for cat_id, sub_name, marks, req_dqc, max_cycle in SUBCATEGORIES_DATA:
        SubCategory.objects.update_or_create(
            category_id=cat_id,
            subcategory_name=sub_name,
            defaults={
                'default_marks': marks,
                'requires_dqc': req_dqc,
                'max_per_cycle': max_cycle
            }
        )

    # Backfill existing submissions for categories with subcategories
    cat_code_to_id = {c[1]: c[0] for c in CATEGORIES_DATA}

    for sub in Submission.objects.all():
        crit = CriteriaItem.objects.filter(pk=sub.criteria_id).select_related('category').first()
        if not crit or not crit.category:
            continue
        c_code = (crit.category.code or '').strip().lower()
        if c_code in ('cat-academics', 'cat-career-advancement', 'cat-documentation'):
            continue  # without subcategories / manual eval

        target_cat_id = cat_code_to_id.get(c_code)
        if not target_cat_id:
            continue

        ev = sub.evidence if isinstance(sub.evidence, dict) else {}
        sub_name = (
            ev.get('subItem') or
            ev.get('researchSubItem') or
            ev.get('prizesSubItem') or
            ev.get('subcategory_name') or
            crit.title or
            ''
        )
        sub_name_str = str(sub_name).strip()

        # Try to find matching SubCategory
        matched_sub = SubCategory.objects.filter(
            category_id=target_cat_id,
            subcategory_name__iexact=sub_name_str
        ).first()

        if not matched_sub and target_cat_id == 8:
            # Prizes outside check
            is_outside = 'outside' in (crit.title or '').lower() or 'outside' in sub_name_str.lower()
            scope = 'Outside' if is_outside else 'Marian'
            for cand in SubCategory.objects.filter(category_id=8):
                if sub_name_str.lower() in cand.subcategory_name.lower() and scope.lower() in cand.subcategory_name.lower():
                    matched_sub = cand
                    break

        if not matched_sub:
            # Substring match
            for cand in SubCategory.objects.filter(category_id=target_cat_id):
                if cand.subcategory_name.lower() in sub_name_str.lower() or sub_name_str.lower() in cand.subcategory_name.lower():
                    matched_sub = cand
                    break

        if matched_sub:
            sub.subcategory_id = matched_sub.id
            sub.calculated_marks = float(matched_sub.default_marks)
            if sub.marks is None or sub.marks == 0:
                sub.marks = int(round(sub.calculated_marks))
            sub.save(update_fields=['subcategory_id', 'calculated_marks', 'marks'])


def reverse_seed(apps, schema_editor):
    SubCategory = apps.get_model('users', 'SubCategory')
    Category = apps.get_model('users', 'Category')
    SubCategory.objects.all().delete()
    Category.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0030_populate_criteria_item_access_levels'),
    ]

    operations = [
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.IntegerField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=100, unique=True)),
            ],
            options={
                'verbose_name': 'Category',
                'verbose_name_plural': 'Categories',
                'db_table': 'categories',
            },
        ),
        migrations.CreateModel(
            name='SubCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subcategory_name', models.CharField(max_length=255)),
                ('default_marks', models.DecimalField(decimal_places=2, default=0.0, max_digits=5)),
                ('requires_dqc', models.BooleanField(default=False)),
                ('max_per_cycle', models.IntegerField(blank=True, null=True)),
                ('category', models.ForeignKey(db_column='category_id', on_delete=django.db.models.deletion.CASCADE, related_name='subcategories', to='users.category')),
            ],
            options={
                'verbose_name': 'Subcategory',
                'verbose_name_plural': 'Subcategories',
                'db_table': 'subcategories',
                'ordering': ['category_id', 'id'],
            },
        ),
        migrations.RunPython(seed_categories_and_subcategories, reverse_seed),
    ]
