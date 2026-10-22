"""First-party request statistics; never store query strings or bearer URLs."""
import ipaddress
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import PageVisit

logger = logging.getLogger(__name__)
PAGE_ROUTES = {'home', 'catalog', 'book-service', 'book-detail', 'order', 'order-success',
               'privacy', 'web-shelf', 'app-shelf', 'account', 'profile', 'login', 'app-login',
               'register', 'email-start', 'install'}


def client_ip(request):
    remote = request.META.get('REMOTE_ADDR', '')
    try:
        peer = ipaddress.ip_address(remote)
        trusted = any(peer in ipaddress.ip_network(value.strip()) for value in settings.ANALYTICS_TRUSTED_PROXIES)
        candidate = request.META.get('HTTP_X_REAL_IP', remote) if settings.BEHIND_PROXY and trusted else remote
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def agent_info(agent):
    browser, version = 'Не определён', ''
    for name, pattern in [('Edge', r'(?:Edg|EdgA|EdgiOS)/([\d.]+)'), ('Opera', r'(?:OPR|Opera)/([\d.]+)'),
            ('Яндекс Браузер', r'YaBrowser/([\d.]+)'), ('Samsung Internet', r'SamsungBrowser/([\d.]+)'),
            ('Firefox', r'(?:Firefox|FxiOS)/([\d.]+)'), ('Chrome', r'(?:Chrome|CriOS)/([\d.]+)'),
            ('Safari', r'Version/([\d.]+).*Safari/'), ('Internet Explorer', r'(?:MSIE |rv:)([\d.]+)')]:
        match = re.search(pattern, agent)
        if match:
            browser, version = name, match.group(1)[:60]
            break
    if browser == 'Chrome' and ('; wv)' in agent or 'PortalBooks/' in agent): browser = 'Android WebView'
    os_name = next((name for name, key in [('Android','Android'), ('iOS','iPhone'), ('iPadOS','iPad'),
        ('Windows','Windows'), ('macOS','Macintosh'), ('Linux','Linux')] if key in agent), 'Не определена')
    device = 'Планшет' if ('iPad' in agent or 'Tablet' in agent or ('Android' in agent and 'Mobile' not in agent)) else 'Телефон' if ('Mobile' in agent or 'iPhone' in agent) else 'Компьютер'
    bot = bool(re.search(r'bot|spider|crawl|headless|slurp|preview|monitor', agent, re.I))
    return browser, version, os_name, device, bot


def referrer_origin(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname: return ''
        host = '['+url.hostname+']' if ':' in url.hostname else url.hostname
        return (url.scheme+'://'+host+(f':{url.port}' if url.port else ''))[:255]
    except ValueError:
        return ''


class VisitMiddleware:
    def __init__(self, get_response): self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not settings.ANALYTICS_ENABLED or request.method != 'GET' or response.status_code != 200 or not response.get('Content-Type', '').startswith('text/html'):
            return response
        route = getattr(getattr(request, 'resolver_match', None), 'url_name', '')
        book = getattr(request, 'visit_book', None)
        if route not in PAGE_ROUTES and book is None: return response
        kind = getattr(request, 'visit_kind', 'page')
        path = reverse('book-video' if kind == 'video' else 'reader', args=[book.slug]) if book else request.path
        agent = request.META.get('HTTP_USER_AGENT', '')[:1024]
        browser, version, os_name, device, bot = agent_info(agent)
        address = client_ip(request)
        user = request.user if request.user.is_authenticated else None
        identity = f'user:{user.pk}' if user else f'{address}:{agent}'
        key = salted_hmac('daily-visits', f'{timezone.localdate()}:{identity}', algorithm='sha256').hexdigest()
        try:
            with transaction.atomic():
                PageVisit.objects.create(kind=kind, path=path[:300], book=book,
                    book_page=getattr(request, 'visit_book_page', None), user=user, ip_address=address,
                    visitor_key=key, browser=browser, browser_version=version, os=os_name,
                    device=device, user_agent=agent, referrer=referrer_origin(request.META.get('HTTP_REFERER', '')), is_bot=bot)
        except DatabaseError:
            logger.warning('Could not save visit statistics', exc_info=False)
        return response


def prune_visits(days=None):
    cutoff = timezone.now() - timedelta(days=days or settings.ANALYTICS_RETENTION_DAYS)
    ids = list(PageVisit.objects.filter(occurred_at__lt=cutoff).values_list('pk', flat=True)[:5000])
    if ids: PageVisit.objects.filter(pk__in=ids).delete()
    return len(ids)
