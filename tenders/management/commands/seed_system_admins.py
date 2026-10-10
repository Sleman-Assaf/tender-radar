# Path: tenders/management/commands/seed_system_admins.py
"""
Creates the 'System Admins' permission group and adds the 'admin' user to it.

Usage:
    python manage.py seed_system_admins
    python manage.py seed_system_admins --user someuser   # add another member
"""
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

GROUP_NAME = 'System Admins'


class Command(BaseCommand):
    help = "Create 'System Admins' group and add the admin user as a member."

    def add_arguments(self, parser):
        parser.add_argument('--user', default='admin',
                            help="Username to add to the group (default: admin)")

    def handle(self, *args, **opts):
        group, created = Group.objects.get_or_create(name=GROUP_NAME)
        self.stdout.write(self.style.SUCCESS(
            f"Group '{GROUP_NAME}' {'created' if created else 'already exists'}."
        ))

        username = opts['user']
        user = User.objects.filter(username=username).first()
        if not user:
            self.stdout.write(self.style.WARNING(
                f"User '{username}' not found — group created, no member added."
            ))
            return

        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(
            f"User '{username}' added to '{GROUP_NAME}'."
        ))
