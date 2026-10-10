# magazine_tenders/management/commands/summarise_news.py
"""
Write AI summaries for the news articles that have none - gently.

    manage.py summarise_news                 newest 40 without a summary, 5 s apart
    manage.py summarise_news --limit 100 --pause 8
    manage.py summarise_news --source ZAWYA
    manage.py summarise_news --all           rewrite summaries that exist (slow)

The sweep writes a batch like this after every run; this is for the backlog
after deploying, or for a source whose summaries you want redone. One AJi AI
call per article, one at a time, with a pause between; stops at the first
failure so a struggling AI host is left alone.
"""
from django.core.management.base import BaseCommand

from magazine_tenders import summaries


class Command(BaseCommand):
    help = 'Write AJi AI summaries for news articles that have none, gently.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None,
                            help='Articles per run (default: MAGAZINE_SUMMARY_BATCH, 40)')
        parser.add_argument('--pause', type=float, default=None,
                            help='Seconds between articles (default: MAGAZINE_SUMMARY_PAUSE, 5)')
        parser.add_argument('--source', default='', help='Magazine code')
        parser.add_argument('--all', action='store_true',
                            help='Rewrite summaries that already exist')

    def handle(self, *args, **opts):
        result = summaries.summarise_backlog(
            limit=opts['limit'], pause=opts['pause'],
            magazine=(opts['source'] or '').strip().upper(), redo=opts['all'],
            report=lambda line: self.stdout.write(line))
        if result['stopped']:
            style = self.style.WARNING if result['written'] else self.style.ERROR
            self.stdout.write(style(result['stopped']))
        self.stdout.write(self.style.SUCCESS(
            f"{result['written']} summary(ies) written ({result['tried']} tried)"))
