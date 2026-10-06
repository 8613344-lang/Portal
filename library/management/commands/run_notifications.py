import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from library.models import RateEvent
from library.notifications import deliver_one, poll_bot

class Command(BaseCommand):
    help = 'Process durable email/Telegram queue and allowed employee bot commands.'
    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=5)
        parser.add_argument('--no-bot', action='store_true')
    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                for _ in range(50):
                    if not deliver_one(): break
                if not options['no_bot']:
                    try: poll_bot()
                    except Exception as exc: self.stderr.write(f'Bot temporarily unavailable: {type(exc).__name__}')
                RateEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()
            except Exception as exc:
                self.stderr.write(f'Worker error: {type(exc).__name__}')
                if options['once']: raise
            if options['once']: break
            time.sleep(max(1, options['interval']))
