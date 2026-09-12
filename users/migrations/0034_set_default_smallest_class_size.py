"""
Migration 0034 — Set default smallest_class_size SystemSetting to 0 for Moderation Formula:
M = (S − P) / N² × (1 + 100 × (N − n))
"""

from django.db import migrations


def set_default_smallest_class_size(apps, schema_editor):
    SystemSetting = apps.get_model('users', 'SystemSetting')
    SystemSetting.objects.update_or_create(
        key='smallest_class_size',
        defaults={'value': '0'}
    )


def reverse_default_smallest_class_size(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0033_sync_master_categories_and_subcategories'),
    ]

    operations = [
        migrations.RunPython(set_default_smallest_class_size, reverse_default_smallest_class_size),
    ]
