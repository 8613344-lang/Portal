import io
import json
import tempfile
from html.parser import HTMLParser
from unittest.mock import patch
from xml.etree import ElementTree as ET

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Book, BookAccess, BookPage


class HeadParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.meta, self.canonical, self.title, self.schemas = {}, '', '', []
        self.in_title, self.in_schema, self.buffer = False, False, ''
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta': self.meta[attrs.get('name', attrs.get('property'))] = attrs.get('content')
        if tag == 'link' and attrs.get('rel') == 'canonical': self.canonical = attrs['href']
        if tag == 'title': self.in_title = True
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.in_schema, self.buffer = True, ''

    def handle_data(self, data):
        if self.in_title: self.title += data
        if self.in_schema: self.buffer += data

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
        if tag == 'script' and self.in_schema:
            self.schemas.append(json.loads(self.buffer))
            self.in_schema = False


@override_settings(SITE_URL='https://qicko.ru', RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND': 'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SearchTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(MEDIA_ROOT=directory.name)
        override.enable(); self.addCleanup(override.disable)
        self.public = Book.objects.create(title='Космическая сказка', slug='seo-public',
            description='Публичная история о доброте и звёздах.', status='published', visibility='public')
        self.private = Book.objects.create(title='НЕПУБЛИЧНОЕ ИМЯ', slug='seo-private', status='published')
        self.draft = Book.objects.create(title='Скрытый черновик', slug='seo-draft', visibility='public')
        self.archived = Book.objects.create(title='Архив', slug='seo-archived', status='archived', visibility='public')
        BookPage.objects.create(book=self.public, position=1, title='Начало', text='Однажды герой отправился к звёздам.')

    def locations(self, response):
        self.assertEqual(response.status_code, 200)
        return [element.text for element in ET.fromstring(response.content).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]

    def test_marketing_pages_have_distinct_metadata_and_canonical_from_config(self):
        descriptions, titles = set(), set()
        for name in ('home', 'catalog', 'order', 'book-service'):
            response = self.client.get(reverse(name), {'utm_source':'test'}, HTTP_HOST='testserver')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['X-Robots-Tag'], 'index, follow')
            head = HeadParser(response.content.decode())
            self.assertEqual(head.canonical, 'https://qicko.ru'+reverse(name))
            self.assertEqual(head.meta['og:url'], head.canonical)
            self.assertEqual(head.meta['robots'], 'index, follow')
            self.assertTrue(head.meta['description'])
            self.assertLessEqual(len(head.meta['description']), 170)
            titles.add(head.title); descriptions.add(head.meta['description'])
        self.assertEqual(len(titles), 4)
        self.assertEqual(len(descriptions), 4)

    def test_robots_and_sitemap_index_reference_only_configured_origin(self):
        robots = self.client.get(reverse('robots'))
        self.assertEqual(robots.status_code, 200)
        self.assertTrue(robots['Content-Type'].startswith('text/plain'))
        self.assertIn('Sitemap: https://qicko.ru/sitemap.xml', robots.content.decode())
        self.assertNotIn('Disallow: /', robots.content.decode())
        index = self.locations(self.client.get(reverse('sitemap')))
        self.assertEqual(index, ['https://qicko.ru/sitemap-pages.xml', 'https://qicko.ru/sitemap-books-1.xml'])
        self.assertNotIn('X-Robots-Tag', robots)
        pages = self.locations(self.client.get(reverse('sitemap-pages')))
        self.assertEqual(set(pages), {'https://qicko.ru'+reverse(name) for name in ('home','catalog','order','book-service')})

    def test_sitemap_only_contains_published_public_pages_and_no_access_tokens(self):
        response = self.client.get(reverse('sitemap-books', args=[1]))
        self.assertEqual(self.locations(response), ['https://qicko.ru'+reverse('book-detail', args=[self.public.slug])])
        for book in (self.public, self.private, self.draft, self.archived):
            self.assertNotIn(str(book.share_token), response.content.decode())
            self.assertNotIn(str(book.video_share_token), response.content.decode())
        for book in (self.private, self.draft, self.archived):
            self.assertNotIn(book.slug, response.content.decode())

    def test_sitemap_is_split_and_catalog_pagination_stays_crawlable(self):
        Book.objects.bulk_create([Book(title=f'Пример {i}', slug=f'seo-example-{i}', status='published', visibility='public') for i in range(20)])
        with patch('library.seo.SITEMAP_BOOKS_PER_FILE', 10):
            index = self.locations(self.client.get(reverse('sitemap')))
            self.assertEqual(len(index), 4)
            urls = []
            for page in (1,2,3):
                batch = self.locations(self.client.get(reverse('sitemap-books', args=[page])))
                self.assertLessEqual(len(batch), 10)
                urls.extend(batch)
            self.assertEqual(len(set(urls)), 21)
            self.assertEqual(self.client.get(reverse('sitemap-books', args=[4])).status_code, 404)
        self.assertIn('https://qicko.ru/books/?page=2', self.locations(self.client.get(reverse('sitemap-pages'))))
        first = self.client.get(reverse('catalog'))
        self.assertContains(first, 'href="?page=2"')
        second = self.client.get(reverse('catalog'), {'page':2})
        self.assertEqual(HeadParser(second.content.decode()).canonical, 'https://qicko.ru/books/?page=2')
        self.assertEqual(second['X-Robots-Tag'], 'index, follow')
        self.assertNotEqual(HeadParser(first.content.decode()).title, HeadParser(second.content.decode()).title)
        for params in ({'q':'сказка'},{'q':''},{'page':999},{'page':'bad'},{'app':1}):
            response = self.client.get(reverse('catalog'), params)
            self.assertIn('noindex', response['X-Robots-Tag'])
            head = HeadParser(response.content.decode())
            self.assertNotIn('og:url', head.meta)

    def test_public_detail_has_book_schema_excerpt_and_safe_dynamic_metadata(self):
        self.public.title = 'История </script><script>alert(1)</script> & "Мир"'
        self.public.save()
        response = self.client.get(reverse('book-detail', args=[self.public.slug]))
        head = HeadParser(response.content.decode())
        graph = head.schemas[0]['@graph']
        self.assertEqual(graph[0]['@type'], 'Book')
        self.assertEqual(graph[0]['name'], self.public.title)
        self.assertEqual(graph[1]['@type'], 'BreadcrumbList')
        self.assertNotContains(response, '</script><script>alert(1)')
        self.assertContains(response, 'Однажды герой отправился к звёздам.')
        self.assertNotContains(response, self.private.title)
        self.assertContains(self.client.get(reverse('catalog')), reverse('book-detail', args=[self.public.slug]))

    def test_withdrawn_books_disappear_even_for_staff_and_qr_holders(self):
        self.client.get(reverse('shared-reader', args=[self.private.share_token]))
        for book in (self.private, self.draft, self.archived):
            response = self.client.get(reverse('book-detail', args=[book.slug]))
            self.assertEqual(response.status_code, 404)
            self.assertIn('noindex', response['X-Robots-Tag'])
        staff = User.objects.create_superuser('seo-editor','seo@example.test','test')
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('book-detail', args=[self.private.slug])).status_code, 404)
        self.public.visibility = 'restricted'; self.public.save()
        self.assertEqual(self.client.get(reverse('book-detail', args=[self.public.slug])).status_code, 404)
        self.assertNotIn(self.public.slug, self.client.get(reverse('sitemap')).content.decode())
        self.assertEqual(self.client.get(reverse('sitemap-books', args=[1])).status_code, 404)

    def test_every_private_utility_or_shared_view_is_noindex(self):
        for name in ('login', 'profile', 'account', 'app-shelf', 'web-shelf', 'privacy', 'health', 'email-start'):
            response = self.client.get(reverse(name))
            self.assertIn('noindex', response['X-Robots-Tag'], name)
        for name, token in (('shared-reader', self.private.share_token), ('shared-video', self.private.video_share_token)):
            response = self.client.get(reverse(name, args=[token]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('noindex', response['X-Robots-Tag'])
            head = HeadParser(response.content.decode())
            self.assertIn('noindex', head.meta['robots'])
            self.assertNotIn('og:url', head.meta)
            self.assertFalse(head.schemas)
        for name in ('reader', 'book-video'):
            for book in (self.public, self.private):
                response = self.client.get(reverse(name, args=[book.slug]))
                self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('noindex', self.client.get('/does-not-exist/')['X-Robots-Tag'])

    def test_binary_exports_and_private_media_are_not_indexed(self):
        user = User.objects.create_user('seo-reader')
        BookAccess.objects.create(book=self.private, user=user)
        self.client.force_login(user)
        response = self.client.get(reverse('book-export', args=[self.private.slug,'pdf']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('noindex', response['X-Robots-Tag'])
        response.close()
        buffer = io.BytesIO(); Image.new('RGB',(10,10),'white').save(buffer, 'PNG')
        self.private.cover.save('cover.png', ContentFile(buffer.getvalue()), save=True)
        response = self.client.get(self.private.cover.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('noindex', response['X-Robots-Tag'])
        response.close()

    @override_settings(GOOGLE_SITE_VERIFICATION='example-verification-code')
    def test_verification_and_bot_responses_do_not_bypass_access(self):
        head = HeadParser(self.client.get(reverse('home')).content.decode())
        self.assertEqual(head.meta['google-site-verification'], 'example-verification-code')
        self.assertContains(self.client.get(reverse('yandex-verification')), 'Verification: 09163709ffc9d3bb')
        ordinary = self.client.get(reverse('book-detail', args=[self.public.slug]))
        bot = self.client.get(reverse('book-detail', args=[self.public.slug]), HTTP_USER_AGENT='Googlebot')
        self.assertEqual(HeadParser(ordinary.content.decode()).canonical, HeadParser(bot.content.decode()).canonical)
        self.assertEqual(Client().get(reverse('book-detail', args=[self.private.slug]), HTTP_USER_AGENT='Googlebot').status_code, 404)
