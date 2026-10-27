from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.db import migrations
from django.utils import timezone


LOCAL_TIME_ZONE = ZoneInfo('Asia/Kolkata')


def convert_datetime_fields(apps, schema_editor, reverse=False):
    database = schema_editor.connection.alias
    utc_zone = datetime_timezone.utc
    MenuItem = apps.get_model('canteen', 'MenuItem')
    FoodOffer = apps.get_model('canteen', 'FoodOffer')

    for model, fields in ((MenuItem, ('opening_time', 'closing_time')),
                          (FoodOffer, ('end_time',))):
        for instance in model.objects.using(database).iterator():
            changed_fields = []
            for field_name in fields:
                value = getattr(instance, field_name)
                if value is None:
                    continue
                if reverse:
                    wall_time = timezone.make_naive(value, LOCAL_TIME_ZONE)
                    value = timezone.make_aware(wall_time, utc_zone)
                else:
                    wall_time = timezone.make_naive(value, utc_zone)
                    value = timezone.make_aware(wall_time, LOCAL_TIME_ZONE)
                setattr(instance, field_name, value)
                changed_fields.append(field_name)
            if changed_fields:
                instance.save(using=database, update_fields=changed_fields)


def localize_datetime_fields(apps, schema_editor):
    convert_datetime_fields(apps, schema_editor)


def restore_utc_datetime_fields(apps, schema_editor):
    convert_datetime_fields(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ('canteen', '0006_alter_menuitem_opening_time'),
    ]

    operations = [
        migrations.RunPython(localize_datetime_fields, restore_utc_datetime_fields),
    ]