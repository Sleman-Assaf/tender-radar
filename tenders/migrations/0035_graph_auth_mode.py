# Path: tenders/migrations/0035_graph_auth_mode.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0034_graph_token_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_auth_mode',
            field=models.CharField(
                choices=[
                    ('APP', 'Application — client credentials (admin consent, no mailbox password)'),
                    ('DELEGATED', 'Delegated — sign in with the mailbox e-mail & password'),
                ],
                default='APP', max_length=12, verbose_name='Authentication mode',
                help_text=('Application: token via client credentials — requires Mail.Send '
                           '*application* permission + admin consent. '
                           'Delegated: token via the mailbox username & password — requires '
                           'Mail.Send *delegated* permission and MFA disabled for that account.'),
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_mailbox_password',
            field=models.CharField(
                blank=True, default='', max_length=300,
                verbose_name='Mailbox password',
                help_text='Only used in Delegated mode — password of the sender mailbox.',
            ),
        ),
    ]
