# tenders/migrations/0011_tender_bookmarked_at.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0010_tender_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='tender',
            name='bookmarked_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When this tender was bookmarked by the user.',
            ),
        ),
    ]
