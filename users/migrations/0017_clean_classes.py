from django.db import migrations


def delete_all_classes(apps, schema_editor):
    Class = apps.get_model('users', 'Class')
    Class.objects.all().delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_class_batch_start_year_class_section_and_more'),
    ]

    operations = [
        migrations.RunPython(delete_all_classes, reverse_code=noop),
    ]
