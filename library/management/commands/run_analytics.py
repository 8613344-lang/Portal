import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from library.analytics import prune_visits


class Command(BaseCommand):
    help = 'Remove old visit statistics according to ANALYTICS_RETENTION_DAYS.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            while prune_visits(): pass
            if options['once']: return
            time.sleep(3600)
