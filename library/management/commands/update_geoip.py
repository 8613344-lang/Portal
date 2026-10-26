"""Download the public DB-IP City Lite database, without visitor requests."""
import gzip
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import maxminddb
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Download DB-IP City Lite MMDB for local approximate city lookup (CC BY 4.0).'

    def handle(self, *args, **options):
        target = Path(settings.ANALYTICS_GEOIP_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        month = date.today().replace(day=1)
        last_error = None
        for _ in range(3):
            url = f'https://download.db-ip.com/free/dbip-city-lite-{month:%Y-%m}.mmdb.gz'
            try:
                with urlopen(Request(url, headers={'User-Agent':'Qicko-GeoIP-Updater/1.0'}), timeout=90) as response:
                    with tempfile.TemporaryFile() as compressed:
                        size = 0
                        while chunk := response.read(1024*1024):
                            size += len(chunk)
                            if size > 200*1024**2: raise CommandError('GeoIP archive is unexpectedly large.')
                            compressed.write(chunk)
                        compressed.seek(0)
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.mmdb', delete=False) as output:
                                temporary = Path(output.name)
                                size = 0
                                with gzip.GzipFile(fileobj=compressed) as archive:
                                    while chunk := archive.read(1024*1024):
                                        size += len(chunk)
                                        if size > 600*1024**2: raise CommandError('GeoIP database is unexpectedly large.')
                                        output.write(chunk)
                            with maxminddb.open_database(str(temporary), mode=maxminddb.MODE_MEMORY) as reader:
                                if 'city' not in reader.metadata().database_type.lower():
                                    raise CommandError('Downloaded database is not a City database.')
                                reader.get('8.8.8.8')
                            temporary.chmod(0o644)
                            temporary.replace(target)
                            temporary = None
                        finally:
                            if temporary is not None: temporary.unlink(missing_ok=True)
                self.stdout.write(self.style.SUCCESS(f'Installed {month:%Y-%m}: {target}'))
                self.stdout.write('IP Geolocation by DB-IP — https://db-ip.com — CC BY 4.0')
                return
            except HTTPError as error:
                last_error = error
                if error.code != 404: break
            except (URLError, OSError, EOFError, maxminddb.InvalidDatabaseError) as error:
                last_error = error
                break
            month = (month-timedelta(days=1)).replace(day=1)
        raise CommandError(f'Could not update GeoIP; previous database was preserved: {last_error}')
