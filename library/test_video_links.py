import io
import tempfile
import uuid
from itertools import product

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader
from django.contrib.admin.sites import AdminSite
from django.core.files.base import ContentFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from .admin import BookAdmin
from .exports import book_pdf, ExportError
from .models import Book, BookPage
from .videos import queue_video


@override_settings(RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    STORAGES={'default': {'BACKEND': 'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class VideoLinkTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        override = override_settings(MEDIA_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        self.book = Book.objects.create(title='Личная история', slug='link-private', status='published')
        self.other = Book.objects.create(title='Другая книга', slug='link-other')
        self.page = BookPage.objects.create(book=self.book, position=1, title='История', text='Текст истории')

    def url(self, name, book=None):
        return reverse(name, args=[(book or self.book).slug])

    def ready(self):
        job = queue_video(self.book, 3)
        job.file.save('video.mp4', ContentFile(b'\x00\x00\x00\x18ftypisom'+b'a'*100), save=False)
        job.state = 'ready'
        job.save()
        return job

    def test_video_link_works_for_every_access_and_status_without_login(self):
        for status, visibility in product(('published', 'draft', 'archived'), ('public', 'restricted')):
            self.book.status, self.book.visibility = status, visibility
            self.book.save()
            self.ready()
            client = Client()
            response = client.get(reverse('shared-video', args=[self.book.video_share_token]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Referrer-Policy'], 'same-origin')
            self.assertIn('no-store', response['Cache-Control'])
            self.assertNotIn('_auth_user_id', client.session)
            self.assertEqual(client.get(self.url('book-video-status')).json()['state'], 'ready')
            response = client.get(self.url('book-video-file'), HTTP_RANGE='bytes=0-11')
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b''.join(response.streaming_content)[4:8], b'ftyp')
            response.close()

    def test_video_grant_isolated_from_book_pdf_media_other_video_and_generation(self):
        self.page.illustration.save('private.png', ContentFile(b'private fixture'), save=True)
        self.ready()
        self.client.get(reverse('shared-video', args=[self.book.video_share_token]))
        for name in ('book-video', 'book-video-status', 'book-video-file'):
            self.assertEqual(self.client.get(self.url(name, self.other)).status_code, 404)
        self.assertEqual(self.client.get(self.url('reader')).status_code, 404)
        self.assertEqual(self.client.get(reverse('book-export', args=[self.book.slug, 'pdf'])).status_code, 404)
        self.assertEqual(self.client.get(self.page.illustration.url).status_code, 404)
        self.assertEqual(self.client.post(self.url('book-video-create'), {'delay':3}).status_code, 404)
        self.assertEqual(self.client.head(self.url('book-video-file')).status_code, 200)
        response = self.client.get(self.url('book-video-file'), {'download':1})
        self.assertIn('attachment', response['Content-Disposition'])
        response.close()
        watch = self.client.get(self.url('book-video'))
        self.assertNotContains(watch, 'video-create')
        self.assertNotContains(watch, 'data-pdf-url')
        self.assertNotIn('shared_books', self.client.session)
        self.assertEqual(Client().get(self.url('book-video-file')).status_code, 404)

    def test_video_link_rotation_revokes_link_and_grant_independently(self):
        old = reverse('shared-video', args=[self.book.video_share_token])
        self.client.get(old)
        self.book.video_share_token = uuid.uuid4()
        self.book.save(update_fields=['video_share_token'])
        self.assertEqual(self.client.get(old).status_code, 404)
        self.assertEqual(self.client.get(self.url('book-video-status')).status_code, 404)
        self.assertEqual(Client().get(reverse('shared-reader', args=[self.book.share_token])).status_code, 200)
        self.assertEqual(self.client.get(reverse('shared-video', args=[self.book.video_share_token])).status_code, 200)
        self.assertEqual(self.client.get(reverse('shared-video', args=[uuid.uuid4()])).status_code, 404)
        self.assertNotEqual(self.book.video_share_token, self.other.video_share_token)

    def test_admin_publishes_both_links_and_can_rotate_only_video(self):
        admin = BookAdmin(Book, AdminSite())
        self.assertIn(self.book.video_share_url, str(admin.video_viewing_link(self.book)))
        self.assertIn('ending_show_video_qr', str(admin.fieldsets))
        token, book_token = self.book.video_share_token, self.book.share_token
        with patch.object(admin, 'message_user'):
            admin.rotate_video_links(RequestFactory().post('/'), Book.objects.filter(pk=self.book.pk))
        self.book.refresh_from_db()
        self.assertNotEqual(token, self.book.video_share_token)
        self.assertEqual(book_token, self.book.share_token)

    def test_pdf_qr_toggles_produce_exact_links_and_no_extra_pages(self):
        for with_picture in (False, True):
            if with_picture:
                image = io.BytesIO()
                Image.new('RGB', (100, 140), '#abcabc').save(image, 'PNG')
                self.book.ending_image.save('ending.png', ContentFile(image.getvalue()), save=False)
                self.book.ending_full_page = True
            for book_qr, video_qr, show_text in product((False, True), repeat=3):
                with self.subTest(picture=with_picture, book=book_qr, video=video_qr, text=show_text):
                    self.book.ending_show_qr, self.book.ending_show_video_qr = book_qr, video_qr
                    self.book.ending_show_text = show_text
                    with book_pdf(self.book, [self.page]) as output:
                        pdf = PdfReader(output)
                        self.assertEqual(len(pdf.pages), 3)
                        ending = pdf.pages[-1]
                        links = [str(a.get_object()['/A']['/URI']) for a in ending.get('/Annots', [])]
                        self.assertEqual(links, ([self.book.share_url] if book_qr else []) +
                                               ([self.book.video_share_url] if video_qr else []))
                        if not show_text:
                            self.assertEqual(ending.extract_text().strip(), '' if with_picture else '3')

    def test_pdf_text_top_is_at_most_qr_top_and_below_both_qrs(self):
        for count in (1, 2):
            self.book.ending_show_video_qr = count == 2
            with book_pdf(self.book, [self.page]) as output:
                data = output.read()
            qr_boxes = [list(a.get_object()['/Rect']) for a in PdfReader(io.BytesIO(data)).pages[-1]['/Annots']]
            document = pdfium.PdfDocument(data)
            page = document[len(document)-1]
            text = page.get_textpage()
            try:
                search = text.search(self.book.ending_title)
                start, length = search.get_next()
                title_top = max(text.get_charbox(i)[3] for i in range(start, start+length))
                search.close()
                limit = min(float(box[1 if count == 2 else 3]) for box in qr_boxes)
                if count == 1: limit -= 3*72/25.4  # Exclude the QR's white quiet zone.
                self.assertLessEqual(title_top, limit)
            finally:
                text.close(); page.close(); document.close()

    def test_reader_shows_two_labeled_codes_and_app_video_link(self):
        self.book.ending_show_video_qr = True
        self.book.save()
        self.client.get(reverse('shared-reader', args=[self.book.share_token]))
        response = self.client.get(self.url('reader'), {'page':3})
        self.assertContains(response, 'ending-layout two-codes')
        self.assertContains(response, self.book.video_share_url)
        self.assertContains(response, 'Смотреть видео')
        response = self.client.get(self.url('reader'), {'page':3, 'app':1})
        self.assertContains(response, self.url('book-video')+'?app=1&amp;device=phone')

    def test_excessive_ending_text_is_rejected_instead_of_clipping(self):
        self.book.ending_show_video_qr = True
        self.book.ending_text = 'Большой текст ' * 3000
        with self.assertRaises(ExportError): book_pdf(self.book, [self.page])
