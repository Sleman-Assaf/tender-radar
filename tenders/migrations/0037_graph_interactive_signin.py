# Path: tenders/migrations/0037_graph_interactive_signin.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenders', '0036_drop_orphan_mobile_number'),
    ]

    operations = [
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_refresh_token',
            field=models.TextField(
                blank=True, default='', editable=False,
                verbose_name='Graph refresh token (interactive sign-in)',
            ),
        ),
        migrations.AddField(
            model_name='integrationsettings',
            name='graph_signed_in_user',
            field=models.CharField(
                blank=True, default='', editable=False, max_length=200,
                verbose_name='Signed-in Microsoft account',
            ),
        ),
        migrations.AlterField(
            model_name='integrationsettings',
            name='graph_auth_mode',
            field=models.CharField(
                choices=[
                    ('APP', 'Application — client credentials (admin consent, no mailbox password)'),
                    ('DELEGATED', 'Delegated — sign in with the mailbox e-mail & password'),
                    ('INTERACTIVE', 'Interactive — Microsoft sign-in popup (refresh token stored)'),
                ],
                default='APP', max_length=12,
                verbose_name='Authentication mode',
                help_text=('Application: token via client credentials — requires Mail.Send '
                           '*application* permission + admin consent. '
                           'Delegated: token via the mailbox username & password — requires '
                           'Mail.Send *delegated* permission and MFA disabled for that account. '
                           'Interactive: user signs in through a Microsoft popup the first time; '
                           'a refresh token keeps it working afterwards (MFA supported).'),
            ),
        ),
    ]
