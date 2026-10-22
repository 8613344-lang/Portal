from pathlib import Path

from PIL import Image
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Book


@override_settings(RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND': 'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class MobileWebTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('mobile-private-user', 'secret-mobile@example.com')
        self.book = Book.objects.create(title='Секретная книга ребёнка', slug='mobile-secret')

    def test_manifest_is_public_stable_and_opens_permission_checked_shelf(self):
        guest = self.client.get(reverse('web-manifest'))
        self.assertEqual(guest.status_code, 200)
        self.assertEqual(guest['Content-Type'], 'application/manifest+json')
        data = guest.json()
        self.assertEqual(data['start_url'], reverse('web-shelf'))
        self.assertEqual(data['scope'], '/')
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['id'], '/')
        self.client.force_login(self.user)
        authenticated = self.client.get(reverse('web-manifest'))
        self.assertEqual(guest.content, authenticated.content)
        for secret in (self.user.email, self.book.title, str(self.book.share_token)):
            self.assertNotIn(secret, authenticated.content.decode())
        for icon in data['icons']:
            path = Path(settings.BASE_DIR) / 'static' / icon['src'].removeprefix('/static/')
            with Image.open(path) as image:
                self.assertEqual(icon['sizes'], f'{image.width}x{image.height}')
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(image.convert('RGBA').getchannel('A').getextrema(), (255, 255))
        self.assertEqual({icon['purpose'] for icon in data['icons']}, {'any', 'maskable'})

    def test_offline_fallback_never_contains_account_or_book_data(self):
        guest = self.client.get(reverse('offline'))
        self.client.force_login(self.user)
        authenticated = self.client.get(reverse('offline'))
        self.assertEqual(guest.content, authenticated.content)
        self.assertFalse(authenticated.cookies)
        self.assertNotContains(authenticated, 'csrfmiddlewaretoken')
        for secret in (self.user.email, self.book.title, str(self.book.share_token)):
            self.assertNotContains(authenticated, secret)
        self.assertContains(authenticated, 'href="/shelf/"')
        self.assertContains(authenticated, 'Нет подключения')
        self.assertIn('noindex', authenticated['X-Robots-Tag'])

    def test_installation_routes_are_safe_and_worker_scope_is_root(self):
        for name in ('install', 'web-manifest', 'service-worker', 'offline'):
            with self.subTest(route=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)
                self.assertEqual(self.client.head(reverse(name)).status_code, 200)
                self.assertEqual(self.client.post(reverse(name)).status_code, 405)
        worker = self.client.get(reverse('service-worker'))
        self.assertEqual(worker['Content-Type'], 'application/javascript')
        self.assertEqual(worker['Service-Worker-Allowed'], '/')
        self.assertEqual(worker['Cache-Control'], 'no-cache')
        self.assertFalse(worker.cookies)

    def test_manual_instructions_work_without_javascript_and_are_always_linked(self):
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'rel="manifest" href="/manifest.webmanifest"')
        self.assertContains(home, 'rel="apple-touch-icon"')
        self.assertContains(home, 'href="/install/"')
        self.assertContains(home, 'class="mobile-nav"')
        instructions = self.client.get(reverse('install'))
        self.assertContains(instructions, 'На iPhone и iPad')
        self.assertContains(instructions, 'На телефоне и планшете Android')
        self.assertContains(instructions, 'На экран Домой')
        self.assertContains(instructions, 'Установить приложение')
        self.assertNotContains(instructions, 'data-install-guide="ios" hidden')
        self.assertNotContains(instructions, 'data-install-guide="android" hidden')
        self.assertNotContains(instructions, 'data-install-offer')
        self.assertNotContains(instructions, 'user-scalable=no')

    def test_native_android_screens_do_not_offer_site_installation(self):
        app = self.client.get(reverse('app-shelf'), {'device': 'tablet'})
        self.assertEqual(app.status_code, 200)
        self.assertNotContains(app, 'data-install-offer')
        self.assertNotContains(app, 'pwa.js')
        self.assertNotContains(app, 'class="mobile-nav"')
