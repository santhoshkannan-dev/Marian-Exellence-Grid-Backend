"""
Migration 0032 — Create ClassLedger model for class index scoring and total marks allocation.
"""

from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0031_create_categories_and_subcategories'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('class_id', models.CharField(max_length=100, unique=True)),
                ('total_marks', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Class Ledger',
                'verbose_name_plural': 'Class Ledgers',
                'db_table': 'class_ledgers',
            },
        ),
    ]
