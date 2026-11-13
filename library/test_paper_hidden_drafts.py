import io
import tempfile
import uuid

from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader

from .exports import book_pdf
from .images import image_url
from .models import Book, BookPage, HiddenVideo, PrintedBookExample
from .validators import validate_video
from .videos import fingerprint


def picture(name='cover.jpg', color='#d7b36a'):
    output = io.BytesIO()
    Image.new('RGB', (420, 560), color).save(output, 'JPEG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/jpeg')


def video(name='book.mp4'):
    # Enough of an ISO BMFF header for upload validation; streaming does not decode it.
    return SimpleUploadedFile(name, b'\x00\x00\x00\x18ftypisom' + b'\x00' * 180,
                              content_type='video/mp4')


@override_settings(
    SITE_URL='https://qicko.ru', RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class PaperHiddenVideoAndDraftTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        media = override_settings(MEDIA_ROOT=directory.name)
        media.enable()
        self.addCleanup(media.disable)
        self.draft = Book.objects.create(
            title='Черновик для согласования', slug='draft-review', status='draft', visibility='restricted')
        self.page = BookPage.objects.create(
            book=self.draft, position=1, title='Первая история', text='Текст для проверки.')

    def test_paper_book_variants_and_filmed_video_are_public_only_when_enabled(self):
        example = PrintedBookExample.objects.create(
            title='Мягкая обложка', description='Настоящий печатный экземпляр',
            cover=picture(), video=video(), position=1)

        home = self.client.get(reverse('home'))
        self.assertContains(home, example.title)
        page = self.client.get(reverse('paper-books'))
        self.assertContains(page, example.title)
        self.assertContains(page, 'paper-book-object')

        cover = self.client.get(image_url(example.cover, 640))
        self.assertEqual((cover.status_code, cover['Content-Type']), (200, 'image/webp'))
        cover.close()
        clip = self.client.get(reverse('paper-book-video', args=[example.pk]), HTTP_RANGE='bytes=0-11')
        self.assertEqual((clip.status_code, clip['Content-Type'], clip['Content-Length']), (206, 'video/mp4', '12'))
        self.assertEqual(len(b''.join(clip.streaming_content)), 12)
        clip.close()

        example.enabled = False
        example.save(update_fields=['enabled'])
        self.assertNotContains(self.client.get(reverse('home')), example.title)
        self.assertNotContains(self.client.get(reverse('paper-books')), example.title)
        self.assertEqual(self.client.get(image_url(example.cover, 640)).status_code, 404)
        self.assertEqual(self.client.get(reverse('paper-book-video', args=[example.pk])).status_code, 404)

    def test_hidden_video_link_is_revocable_and_never_unlocks_the_book(self):
        item = HiddenVideo.objects.create(
            title='Приветствие автора', description='Перед началом истории',
            video=video('intro.mp4'), book=self.draft, placement='start')
        page_url = reverse('hidden-video', args=[item.share_token])
        file_url = reverse('hidden-video-file', args=[item.share_token])

        response = self.client.get(page_url)
        self.assertContains(response, item.title)
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        clip = self.client.get(file_url, HTTP_RANGE='bytes=4-15')
        self.assertEqual((clip.status_code, clip['Content-Type'], clip['Content-Length']), (206, 'video/mp4', '12'))
        clip.close()
        self.assertEqual(self.client.head(file_url).status_code, 200)

        self.assertEqual(self.client.get(reverse('reader', args=[self.draft.slug])).status_code, 404)
        self.assertEqual(self.client.get(reverse('book-export', args=[self.draft.slug, 'pdf'])).status_code, 404)
        self.assertEqual(self.client.get(item.video.url).status_code, 404)

        old_token = item.share_token
        item.share_token = uuid.uuid4()
        item.save(update_fields=['share_token'])
        self.assertEqual(self.client.get(reverse('hidden-video', args=[old_token])).status_code, 404)
        self.assertEqual(self.client.get(reverse('hidden-video-file', args=[old_token])).status_code, 404)
        self.assertEqual(self.client.get(reverse('hidden-video', args=[item.share_token])).status_code, 200)
        item.enabled = False
        item.save(update_fields=['enabled'])
        self.assertEqual(self.client.get(reverse('hidden-video', args=[item.share_token])).status_code, 404)

    def test_attached_video_appears_only_on_selected_edge_of_book(self):
        item = HiddenVideo.objects.create(
            title='Особое вступление', video=video('edge.mp4'), book=self.draft, placement='start')
        share = reverse('shared-reader', args=[self.draft.share_token])
        self.assertContains(self.client.get(share), item.title)
        self.assertNotContains(self.client.get(share, {'page': 2}), item.title)
        self.assertNotContains(self.client.get(share, {'page': 3}), item.title)

        item.placement = 'end'
        item.save(update_fields=['placement'])
        self.assertNotContains(self.client.get(share), item.title)
        self.assertContains(self.client.get(share, {'page': 3}), item.title)

    def test_hidden_video_requires_book_when_placed_and_validates_real_container(self):
        unattached = HiddenVideo(title='Без книги', video=video(), placement='start')
        with self.assertRaises(ValidationError):
            unattached.full_clean()
        validate_video(video())
        for upload in (
            SimpleUploadedFile('bad.avi', b'video'),
            SimpleUploadedFile('fake.mp4', b'not an mp4 container'),
        ):
            with self.assertRaises(ValidationError):
                validate_video(upload)

    def test_every_draft_reader_and_pdf_sheet_has_a_watermark(self):
        self.draft.cover.save('draft-cover.jpg', picture(), save=True)
        self.page.illustration.save('draft-page.jpg', picture('page.jpg', '#93b3c8'), save=True)
        share = reverse('shared-reader', args=[self.draft.share_token])
        for number in (1, 2, 3):
            response = self.client.get(share, {'page': number})
            self.assertContains(response, 'draft-watermarked')

        with book_pdf(self.draft, [self.page]) as output:
            pdf_pages = PdfReader(output).pages
            self.assertEqual(len(pdf_pages), 3)
            for sheet in pdf_pages:
                self.assertIn('ЧЕРНОВИК', sheet.extract_text())

        draft_fingerprint = fingerprint(self.draft)
        self.draft.status = 'published'
        self.draft.save(update_fields=['status'])
        self.assertNotEqual(fingerprint(self.draft), draft_fingerprint)

        published = Book.objects.create(
            title='Готовая книга', slug='ready-book', status='published', visibility='public')
        ready_page = BookPage.objects.create(book=published, position=1, title='Готово', text='Чистый лист')
        with book_pdf(published, [ready_page]) as output:
            self.assertTrue(all('ЧЕРНОВИК' not in (sheet.extract_text() or '') for sheet in PdfReader(output).pages))
