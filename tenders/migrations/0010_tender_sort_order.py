# tenders/migrations/0010_tender_sort_order.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0009_userprofile_order_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='tender',
            name='sort_order',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Display order within same scrape run. WorldBank: 0=known deadline, 9999=unknown.',
            ),
        ),
    ]
