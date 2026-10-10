# Hand-written, 2026-10-01: the AI-written summary shown on the News card.
#
# Two plain text columns, both empty by default. SQLite adds them in place;
# no row changes. Reversible: `migrate magazine_tenders 0005` drops them, and
# nothing is lost that `manage.py summarise_news` cannot write again.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('magazine_tenders', '0005_zawya_image_host'),
    ]

    operations = [
        migrations.AddField(
            model_name='magazinetendermeta',
            name='summary',
            field=models.TextField(blank=True, help_text='Two or three sentences written by AJi AI when the article was stored, leading with the figures; empty falls back to the publisher\'s excerpt'),
        ),
        migrations.AddField(
            model_name='magazinetendermeta',
            name='summary_model',
            field=models.CharField(blank=True, help_text='The model AJi AI reported for the summary', max_length=60),
        ),
    ]
