from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Book, BookAccess, BookPage


@override_settings(
    RATE_LIMIT_ENABLED=False,
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class HomeAudioPreviewTests(TestCase):
    def test_no_recording_has_empty_preview(self):
        Book.objects.create(title='История', slug='story', status='published', visibility='public')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['preview_audio_url'], '')
        self.assertEqual(response.context['preview_audio_title'], '')
        self.assertIsNone(response.context['preview_book'])

    def test_published_public_cover_recording_can_be_previewed(self):
        book = Book.objects.create(
            title='Открытая история', slug='public', status='published', visibility='public',
            cover_audio='book/cover.mp3',
        )
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['preview_book'], book)
        self.assertEqual(response.context['preview_audio_url'], book.cover_audio.url)
        self.assertEqual(response.context['preview_audio_title'], book.title)

    def test_page_recording_is_used_when_no_cover_recording_exists(self):
        book = Book.objects.create(title='История', slug='story', status='published', visibility='public')
        page = BookPage.objects.create(book=book, position=1, title='Глава', audio='bookpage/recording.wav')
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['preview_book'], book)
        self.assertEqual(response.context['preview_audio_url'], page.audio.url)

    def test_private_and_unpublished_recordings_are_never_public_previews(self):
        user = User.objects.create_superuser('editor', 'editor@example.com', 'example-password')
        for slug, status, visibility in (
            ('restricted', 'published', 'restricted'),
            ('draft', 'draft', 'public'),
            ('archived', 'archived', 'public'),
        ):
            book = Book.objects.create(
                title=slug, slug=slug, status=status, visibility=visibility,
                cover_audio=f'book/{slug}.mp3',
            )
            BookPage.objects.create(book=book, position=1, title='Глава', audio=f'bookpage/{slug}.wav')
            BookAccess.objects.create(book=book, user=user)
        self.client.force_login(user)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['preview_audio_url'], '')
        self.assertIsNone(response.context['preview_book'])
