import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from library.videos import process_one


class Command(BaseCommand):
    help = 'Create queued private MP4 books; safely resume expired worker leases.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=3)

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            worked = process_one()
            if options['once']:
                return
            if not worked:
                time.sleep(max(1, options['interval']))
