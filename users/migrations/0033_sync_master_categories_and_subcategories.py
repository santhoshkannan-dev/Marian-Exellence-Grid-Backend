"""
Migration 0033 — Sync master categories, subcategories, and criteria catalog according to
Detailed Category & Subcategory Master Specification.
Permanently removes deprecated categories (cat-documentation) and deprecated subcategories.
"""

from decimal import Decimal
from django.db import migrations


MASTER_CATEGORIES = [
    (1, 'cat-academics', 'Academics', 'dqc_only', False),
    (2, 'cat-online-courses', 'Online Courses', 'all_students', False),
    (3, 'cat-competitive-exams', 'Competitive Exams', 'all_students', False),
    (4, 'cat-internships', 'Internships', 'all_students', False),
    (5, 'cat-scholarships', 'Scholarships', 'all_students', False),
    (6, 'cat-research', 'Research', 'all_students', False),
    (7, 'cat-startups', 'Startups', 'all_students', False),
    (8, 'cat-prizes', 'Prizes Won', 'hybrid', False),
    (9, 'cat-programs-organized', 'Programs Organized', 'dqc_only', False),
    (10, 'cat-leadership', 'Leaderships', 'dqc_only', False),
    (11, 'cat-social-responsibility', 'Social Responsibilities', 'dqc_only', False),
    (12, 'cat-career-advancement', 'Career Advancement', 'hybrid', True),
]

MASTER_SUBCATEGORIES = [
    # Cat 1: Academics
    (1, 'Sem Result (End Semester Examination)', Decimal('0.00'), True, 1),

    # Cat 2: Online Courses
    (2, 'Swayam / NPTEL Course', Decimal('5.00'), False, 3),
    (2, 'MOOC Course', Decimal('2.00'), False, 3),

    # Cat 3: Competitive Exams
    (3, 'JRF Passed', Decimal('20.00'), False, 1),
    (3, 'NET Passed', Decimal('10.00'), False, 1),
    (3, 'Any Other Relevant Exam (IELTS, PET, Language Specific, etc.)', Decimal('3.00'), False, None),
    (3, 'Participation in Relevant Exam (UPSC / PSC Exams)', Decimal('1.00'), False, 3),

    # Cat 4: Internships
    (4, 'Offline Internship (Min. 1 month)', Decimal('5.00'), False, None),
    (4, 'Online Internship (Min. 1 month)', Decimal('3.00'), False, None),

    # Cat 5: Scholarships
    (5, 'International Level Scholarship', Decimal('20.00'), False, None),
    (5, 'National Level Scholarship', Decimal('10.00'), False, None),
    (5, 'State Level Scholarship', Decimal('5.00'), False, None),
    (5, 'District Level Scholarship', Decimal('2.00'), False, None),

    # Cat 6: Research
    (6, 'Scopus / Web of Science', Decimal('10.00'), False, None),
    (6, 'Conference Proceeding / Peer reviewed article', Decimal('5.00'), False, None),
    (6, 'Paper Presentation - Outside Marian College', Decimal('5.00'), False, None),
    (6, 'Paper Presentation - Inside Marian College', Decimal('3.00'), False, None),
    (6, 'Patents - Utility', Decimal('10.00'), False, None),
    (6, 'Patents - Design', Decimal('5.00'), False, None),
    (6, 'Book Publications - Book', Decimal('10.00'), False, None),
    (6, 'Book Publications - Book Chapter', Decimal('5.00'), False, None),
    (6, 'Book Publications - Article', Decimal('2.00'), False, None),
    (6, 'Funded Projects - International', Decimal('20.00'), False, None),
    (6, 'Funded Projects - National', Decimal('10.00'), False, None),
    (6, 'Funded Projects - State', Decimal('5.00'), False, None),
    (6, 'Funded Projects - Any Other', Decimal('3.00'), False, None),

    # Cat 7: Startups
    (7, 'Government-Registered Start-up', Decimal('10.00'), False, None),

    # Cat 8: Prizes Won (Scoped names & Marian/Outside variants)
    (8, '1st Prize (Individual) - Marian', Decimal('10.00'), False, None),
    (8, '2nd Prize (Individual) - Marian', Decimal('5.00'), False, None),
    (8, '3rd Prize (Individual) - Marian', Decimal('3.00'), False, None),
    (8, '1st Prize (Group) - Marian', Decimal('5.00'), True, None),
    (8, '2nd Prize (Group) - Marian', Decimal('3.00'), True, None),
    (8, '3rd Prize (Group) - Marian', Decimal('2.00'), True, None),
    (8, '1st Prize (Individual) - Outside', Decimal('15.00'), False, None),
    (8, '2nd Prize (Individual) - Outside', Decimal('10.00'), False, None),
    (8, '3rd Prize (Individual) - Outside', Decimal('5.00'), False, None),
    (8, 'Participation (Individual) - Outside', Decimal('3.00'), False, None),
    (8, '1st Prize (Group) - Outside', Decimal('10.00'), True, None),
    (8, '2nd Prize (Group) - Outside', Decimal('5.00'), True, None),
    (8, '3rd Prize (Group) - Outside', Decimal('3.00'), True, None),
    (8, 'Participation (Group) - Outside', Decimal('2.00'), True, None),

    # Cat 9: Programs Organized
    (9, 'Intercollegiate', Decimal('5.00'), True, 1),
    (9, 'Intra - Collegiate', Decimal('3.00'), True, 1),
    (9, 'Class Magazine', Decimal('5.00'), True, 1),

    # Cat 10: Leaderships
    (10, 'MCSC Executive Body Position', Decimal('5.00'), True, None),
    (10, 'SAHYA Executive Body Position', Decimal('5.00'), True, None),
    (10, 'Clubs & Associations Leadership Position', Decimal('5.00'), True, None),
    (10, 'Innovative / Sustainable Suggestion', Decimal('5.00'), True, None),

    # Cat 11: Social Responsibilities
    (11, 'Coordination of Event (Community Action / Outreach)', Decimal('5.00'), True, None),
    (11, 'Participation in Event', Decimal('3.00'), True, None),
    (11, 'News Media Coverage (Excluding Social Media)', Decimal('3.00'), True, None),

    # Cat 12: Career Advancement
    (12, 'Library - Regular Footfall (Biometric / Entry)', Decimal('0.00'), True, None),
    (12, 'Library - Academic & Career Books Issued/Read', Decimal('0.00'), True, None),
    (12, 'Repository Creation (Drive / GitHub / LMS / Website)', Decimal('0.00'), True, None),
    (12, 'LinkedIn - Profile Completion (Active Profile)', Decimal('0.00'), False, None),
]


def sync_master_categories(apps, schema_editor):
    Category = apps.get_model('users', 'Category')
    SubCategory = apps.get_model('users', 'SubCategory')
    CriteriaCategory = apps.get_model('users', 'CriteriaCategory')
    CriteriaItem = apps.get_model('users', 'CriteriaItem')
    CriteriaRule = apps.get_model('users', 'CriteriaRule')

    # 1. Permanently remove cat-documentation
    doc_crit_cats = CriteriaCategory.objects.filter(code='cat-documentation')
    for dcc in doc_crit_cats:
        CriteriaItem.objects.filter(category=dcc).delete()
    doc_crit_cats.delete()
    Category.objects.filter(code='cat-documentation').delete()
    Category.objects.filter(name__iexact='Documentation').delete()

    # 2. Permanently remove deprecated Career Advancement items
    deprecated_items = CriteriaItem.objects.filter(
        title__in=[
            'LinkedIn - Skill Badges Earned',
            'LinkedIn - Micro-credentials / Learning Certifications',
            'Class Activity Report & Documents'
        ]
    )
    for di in deprecated_items:
        CriteriaRule.objects.filter(item=di).delete()
    deprecated_items.delete()

    # 3. Clean up and sync Category table (1-12)
    Category.objects.exclude(id__in=[c[0] for c in MASTER_CATEGORIES]).delete()
    for cid, code, name, access, is_man in MASTER_CATEGORIES:
        Category.objects.update_or_create(
            id=cid,
            defaults={'code': code, 'name': name}
        )

    # 4. Clean up SubCategory table
    valid_sub_names_by_cat = {}
    for cat_id, sub_name, marks, req_dqc, max_cycle in MASTER_SUBCATEGORIES:
        valid_sub_names_by_cat.setdefault(cat_id, set()).add(sub_name.lower().strip())

    # Remove subcategories not in the master list
    for sub in list(SubCategory.objects.all()):
        valid_names = valid_sub_names_by_cat.get(sub.category_id, set())
        if sub.subcategory_name.lower().strip() not in valid_names:
            sub.delete()

    # Insert or update master subcategories
    for cat_id, sub_name, marks, req_dqc, max_cycle in MASTER_SUBCATEGORIES:
        SubCategory.objects.update_or_create(
            category_id=cat_id,
            subcategory_name=sub_name,
            defaults={
                'default_marks': marks,
                'requires_dqc': req_dqc,
                'max_per_cycle': max_cycle
            }
        )

    # 5. Sync CriteriaCategory (12 categories)
    CriteriaCategory.objects.exclude(code__in=[c[1] for c in MASTER_CATEGORIES]).delete()
    for cid, code, name, access, is_man in MASTER_CATEGORIES:
        # access_level mapping: 'hybrid' -> 'all_students' on category level (items have specific rules)
        cat_access = 'student_rep_only' if access == 'dqc_only' else 'all_students'
        CriteriaCategory.objects.update_or_create(
            code=code,
            defaults={
                'category': name,
                'access_level': cat_access,
                'is_manual_eval': is_man
            }
        )

    # 6. Ensure CriteriaItems exist and are properly configured for each category
    cat_lookup = {c.code: c for c in CriteriaCategory.objects.all()}

    # Cat 1: Academics
    c1 = cat_lookup.get('cat-academics')
    if c1:
        # Clean up legacy duplicate items in academics
        CriteriaItem.objects.filter(category=c1).exclude(
            title='Sem Result (End Semester Examination)'
        ).delete()
        item1, _ = CriteriaItem.objects.update_or_create(
            category=c1,
            title='Sem Result (End Semester Examination)',
            defaults={
                'type': 'academic_grades',
                'marks': 0.0,
                'access_level': 'student_rep_only',
                'is_manual_eval': False,
                'rules_json': {
                    '90_above': 5.0,
                    '80_90': 4.0,
                    '70_80': 3.0,
                    'fail': -1.0,
                    'pass_percentage_ranges': [
                        {'min': 90.01, 'max': 100.0, 'marks': 5.0},
                        {'min': 80.01, 'max': 90.0, 'marks': 4.0},
                        {'min': 70.01, 'max': 80.0, 'marks': 3.0},
                        {'min': 60.01, 'max': 70.0, 'marks': 2.0},
                        {'min': 50.01, 'max': 60.0, 'marks': 1.0},
                        {'min': 0.0, 'max': 50.0, 'marks': 0.0}
                    ],
                    'fields': {
                        'count_90_above': True,
                        'count_80_90': True,
                        'count_70_80': True,
                        'count_fail': True,
                        'pass_percentage': True,
                        'proof_url': True,
                        'description': True
                    },
                    'max_per_cycle': 1
                }
            }
        )

    # Cat 8: Prizes Won
    c8 = cat_lookup.get('cat-prizes')
    if c8:
        c8.category = 'Prizes Won'
        c8.save(update_fields=['category'])
        CriteriaItem.objects.update_or_create(
            category=c8,
            title='From Marian College',
            defaults={
                'type': 'count',
                'marks': 0.0,
                'access_level': 'all_students',
                'is_manual_eval': False,
                'rules_json': {
                    'subItems': {
                        '1st Prize (Individual)': 10.0,
                        '2nd Prize (Individual)': 5.0,
                        '3rd Prize (Individual)': 3.0,
                        '1st Prize (Group)': 5.0,
                        '2nd Prize (Group)': 3.0,
                        '3rd Prize (Group)': 2.0
                    },
                    'dqcSubItems': ['1st Prize (Group)', '2nd Prize (Group)', '3rd Prize (Group)'],
                    'fields': {'proof_url': True, 'description': True}
                }
            }
        )
        CriteriaItem.objects.update_or_create(
            category=c8,
            title='Outside Marian College',
            defaults={
                'type': 'count',
                'marks': 0.0,
                'access_level': 'all_students',
                'is_manual_eval': False,
                'rules_json': {
                    'subItems': {
                        '1st Prize (Individual)': 15.0,
                        '2nd Prize (Individual)': 10.0,
                        '3rd Prize (Individual)': 5.0,
                        'Participation (Individual)': 3.0,
                        '1st Prize (Group)': 10.0,
                        '2nd Prize (Group)': 5.0,
                        '3rd Prize (Group)': 3.0,
                        'Participation (Group)': 2.0
                    },
                    'dqcSubItems': [
                        '1st Prize (Group)', '2nd Prize (Group)',
                        '3rd Prize (Group)', 'Participation (Group)'
                    ],
                    'fields': {'proof_url': True, 'description': True}
                }
            }
        )

    # Cat 9: Programs Organized
    c9 = cat_lookup.get('cat-programs-organized')
    if c9:
        CriteriaItem.objects.filter(category=c9, title__iexact='Intracollegiate').delete()
        for title, marks in [('Intercollegiate', 5.0), ('Intra - Collegiate', 3.0), ('Class Magazine', 5.0)]:
            CriteriaItem.objects.update_or_create(
                category=c9,
                title=title,
                defaults={
                    'type': 'count',
                    'marks': marks,
                    'access_level': 'student_rep_only',
                    'is_manual_eval': False,
                    'rules_json': {
                        'max_per_cycle': 1,
                        'fields': {'event_name': True, 'event_id': True, 'description': True}
                    }
                }
            )

    # Cat 12: Career Advancement
    c12 = cat_lookup.get('cat-career-advancement')
    if c12:
        valid_c12_titles = [
            'Library - Regular Footfall (Biometric / Entry)',
            'Library - Academic & Career Books Issued/Read',
            'Repository Creation (Drive / GitHub / LMS / Website)',
            'LinkedIn - Profile Completion (Active Profile)'
        ]
        CriteriaItem.objects.filter(category=c12).exclude(title__in=valid_c12_titles).delete()
        for title, acc in [
            ('Library - Regular Footfall (Biometric / Entry)', 'student_rep_only'),
            ('Library - Academic & Career Books Issued/Read', 'student_rep_only'),
            ('Repository Creation (Drive / GitHub / LMS / Website)', 'student_rep_only'),
            ('LinkedIn - Profile Completion (Active Profile)', 'all_students')
        ]:
            CriteriaItem.objects.update_or_create(
                category=c12,
                title=title,
                defaults={
                    'type': 'fixed' if 'Repository' in title or 'LinkedIn' in title else 'count',
                    'marks': 0.0,
                    'access_level': acc,
                    'is_manual_eval': True,
                    'rules_json': {
                        'fields': {'proof_url': True, 'description': True}
                    }
                }
            )


def reverse_sync(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0032_classledger'),
    ]

    operations = [
        migrations.RunPython(sync_master_categories, reverse_sync),
    ]
