"""Local IP lookups; never submit visitor IP addresses to a web service."""
import ipaddress
import threading
from functools import lru_cache
from pathlib import Path

import maxminddb
from django.conf import settings

_lock = threading.RLock()
_reader = None
_signature = None


def database_signature():
    try:
        path = Path(settings.ANALYTICS_GEOIP_PATH)
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def available():
    signature = database_signature()
    return bool(signature and _lookup_cached('8.8.8.8', signature)['geo_checked'])


def lookup(address):
    empty = {'city':'', 'region':'', 'country':'', 'geo_source':'', 'geo_checked':False}
    try:
        if not address or not ipaddress.ip_address(address).is_global:
            return {**empty, 'geo_checked':True}
    except ValueError:
        return {**empty, 'geo_checked':True}
    signature = database_signature()
    if signature is None: return empty
    return dict(_lookup_cached(address, signature))


@lru_cache(maxsize=4096)
def _lookup_cached(address, signature):
    global _reader, _signature
    empty = {'city':'', 'region':'', 'country':'', 'geo_source':'', 'geo_checked':False}
    with _lock:
        try:
            if _signature != signature:
                if _reader is not None: _reader.close()
                _reader = None
                _signature = None
                _reader = maxminddb.open_database(signature[0], mode=maxminddb.MODE_MEMORY)
                _signature = signature
            data = _reader.get(address) or {}
        except (OSError, ValueError, maxminddb.InvalidDatabaseError):
            return empty
    def name(value):
        if not isinstance(value, dict): return ''
        names = value.get('names', {})
        if not isinstance(names, dict): return ''
        return str(names.get('ru') or names.get('en') or '')[:120]
    if not isinstance(data, dict): return empty
    regions = data.get('subdivisions') or [{}]
    if not isinstance(regions, list): regions = [{}]
    return {'city': name(data.get('city', {})), 'region':name(regions[0]),
            'country':name(data.get('country', {})), 'geo_source':'DB-IP City Lite · https://db-ip.com',
            'geo_checked':True}


def enrich_visits(batch=500):
    from .models import PageVisit
    if not available(): return 0
    rows = list(PageVisit.objects.filter(geo_checked=False).order_by('pk')[:batch])
    for row in rows:
        for key, value in lookup(row.ip_address).items(): setattr(row, key, value)
    if rows:
        PageVisit.objects.bulk_update(rows, ['city','region','country','geo_source','geo_checked'])
    return sum(row.geo_checked for row in rows)
