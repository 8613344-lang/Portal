from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Book, BookAccess, BookPage


@override_settings(
    RATE_LIMIT_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STATIC_ROOT=None,
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class WebShelfTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('shelf-reader', email='shelf@example.com')
        self.other = User.objects.create_user('other-reader')
        self.public = Book.objects.create(title='Открытая история', slug='shelf-public', visibility='public', status='published')
        self.personal = Book.objects.create(title='Моя история', slug='shelf-personal', status='published')
        self.secret = Book.objects.create(title='Чужая история', slug='shelf-secret', status='published')
        self.draft = Book.objects.create(title='Неопубликованная история', slug='shelf-draft', visibility='public')
        BookAccess.objects.create(book=self.personal, user=self.user)
        BookAccess.objects.create(book=self.secret, user=self.other)
        BookAccess.objects.create(book=self.draft, user=self.user)

    def test_anonymous_shelf_contains_only_published_public_books(self):
        response = self.client.get(reverse('web-shelf'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.public.title)
        for book in (self.personal, self.secret, self.draft):
            self.assertNotContains(response, book.title)
        self.assertContains(response, '/accounts/login/?next=/shelf/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTemplateUsed(response, 'base.html')

    def test_personal_shelf_comes_first_and_public_assignments_are_not_duplicated(self):
        BookAccess.objects.create(book=self.public, user=self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('web-shelf'))
        mine = list(response.context['personal_books'])
        everyone = list(response.context['public_books'])
        self.assertEqual({book.pk for book in mine}, {self.personal.pk, self.public.pk})
        self.assertEqual(everyone, [])
        self.assertNotContains(response, self.secret.title)
        self.assertNotContains(response, self.draft.title)
        content = response.content.decode()
        self.assertLess(content.index('id="personal-shelf-title"'), content.index('id="public-shelf-title"'))
        self.assertContains(response, reverse('reader', args=[self.personal.slug]) + '?shelf=1')
        self.assertNotContains(response, '?app=1')

    def test_reader_autostarts_with_safe_website_return_without_granting_access(self):
        url = reverse('reader', args=[self.public.slug])
        ordinary = self.client.get(url)
        response = self.client.get(url, {'shelf': '1', 'next': 'https://outside.invalid/'})
        self.assertFalse(ordinary.context['shared'])
        self.assertTrue(response.context['shared'])
        self.assertTrue(response.context['shelf_mode'])
        self.assertFalse(response.context['app_mode'])
        self.assertEqual(response.context['base_template'], 'base.html')
        self.assertEqual(response.context['shelf_return_url'], reverse('web-shelf'))
        self.assertContains(response, 'data-autostart="true"')
        self.assertContains(response, 'data-shelf-return="/shelf/"')
        self.assertContains(response, 'href="/shelf/">← К книгам</a>')
        self.assertEqual(self.client.get(reverse('reader', args=[self.personal.slug]), {'shelf': '1'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('reader', args=[self.draft.slug]), {'shelf': '1'}).status_code, 404)
        self.assertNotIn('shared_books', self.client.session)

    def test_authenticated_reader_keeps_shelf_context_on_page_changes(self):
        BookPage.objects.create(book=self.personal, position=1, title='Первая глава', text='История')
        self.client.force_login(self.user)
        response = self.client.get(reverse('reader', args=[self.personal.slug]), {'shelf': '1', 'page': '2'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['number'], 2)
        self.assertTrue(response.context['shelf_mode'])
        self.assertEqual(response.context['shelf_return_url'], '/shelf/')
        self.assertContains(response, 'href="?shelf=1&amp;page=1"')
        self.assertContains(response, 'href="?shelf=1&amp;page=3"')
        # Contents and normal next/previous navigation both preserve the shelf origin.
        content = response.content.decode()
        contents = content.split('<aside class="contents">', 1)[1].split('</aside>', 1)[0]
        navigation = content.split('<nav class="page-navigation"', 1)[1].split('>', 1)[1].split('</nav>', 1)[0]
        for fragment in (contents, navigation):
            self.assertIn('href="?shelf=1&amp;page=1"', fragment)
            self.assertIn('href="?shelf=1&amp;page=3"', fragment)

    def test_both_shelves_are_paginated_independently(self):
        for number in range(25):
            Book.objects.create(title=f'Общая {number}', slug=f'public-{number}', visibility='public', status='published')
            book = Book.objects.create(title=f'Личная {number}', slug=f'personal-{number}', status='published')
            BookAccess.objects.create(book=book, user=self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse('web-shelf'), {'mine': '2', 'public': '1'})
        self.assertEqual(response.context['personal_books'].number, 2)
        self.assertEqual(response.context['public_books'].number, 1)
        self.assertEqual(len(response.context['personal_books']), 2)
        self.assertEqual(len(response.context['public_books']), 24)
        self.assertContains(response, '?mine=2&amp;public=2')
        response = self.client.get(reverse('web-shelf'), {'mine': '1', 'public': '2'})
        self.assertEqual(response.context['personal_books'].number, 1)
        self.assertEqual(response.context['public_books'].number, 2)

    def test_cover_fallback_and_original_android_mode(self):
        page = BookPage.objects.create(book=self.public, position=1, title='Начало', illustration='bookpage/test-cover.jpg')
        response = self.client.get(reverse('web-shelf'))
        from .images import image_url
        self.assertEqual(response.context['public_books'][0].app_cover, image_url(page.illustration, 640))
        response = self.client.get(reverse('app-shelf'), {'device': 'tablet'})
        self.assertTemplateUsed(response, 'app_base.html')
        self.assertEqual(response.context['device'], 'tablet')
        self.assertEqual(response.context['public_books'][0].app_reader, reverse('reader', args=[self.public.slug]) + '?app=1&device=tablet')
        reader = self.client.get(reverse('reader', args=[self.public.slug]), {'app': '1', 'shelf': '1', 'device': 'tablet'})
        self.assertTrue(reader.context['app_mode'])
        self.assertFalse(reader.context['shelf_mode'])
        self.assertEqual(reader.context['shelf_return_url'], '')
        self.assertContains(reader, 'data-shelf-return=""')
        self.assertContains(reader, 'data-app="true"')
