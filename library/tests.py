import tempfile
import uuid
import wave
import subprocess
from pathlib import Path
from io import BytesIO
from unittest.mock import patch
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from .models import Book, BookAccess, BookPage, Notification, NotificationRecipient, Order
from .notifications import deliver_one, handle_bot_update

def wav_file():
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as file:
        file.setnchannels(1); file.setsampwidth(2); file.setframerate(8000)
        file.writeframes(b'\x00\x00' * 8000)
    return SimpleUploadedFile('page.wav', buffer.getvalue(), content_type='audio/wav')

def image_file():
    buffer = BytesIO()
    Image.new('RGB', (50, 50), '#e9dab7').save(buffer, 'JPEG')
    return SimpleUploadedFile('child.jpg', buffer.getvalue(), content_type='image/jpeg')

@override_settings(RATE_LIMIT_ENABLED=False, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], STORAGES={'default': {'BACKEND': 'library.storage.PrivateStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PlatformTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        static_root = Path(cls.media.name) / 'static'
        static_root.mkdir()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media.name, STATIC_ROOT=static_root)
        cls.media_override.enable()
        super().setUpClass()
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.media_override.disable(); cls.media.cleanup()
    def setUp(self):
        self.a = User.objects.create_user('reader-a', password='LongPassword-test-738')
        self.b = User.objects.create_user('reader-b', password='LongPassword-test-738')
        self.staff = User.objects.create_superuser('editor', 'editor@example.com', 'LongPassword-test-738')
        self.public = Book.objects.create(title='Публичная книга', slug='public', status='published', visibility='public')
        self.private = Book.objects.create(title='Секретная книга', slug='private', status='published', visibility='restricted')
        self.draft = Book.objects.create(title='Черновик', slug='draft', status='draft', visibility='public')
        self.page = BookPage.objects.create(book=self.private, position=1, title='Личная страница', text='Секретный текст', audio=wav_file(), illustration=image_file())
        BookPage.objects.create(book=self.public, position=1, title='Открытая страница', text='Добрая история')
        BookAccess.objects.create(book=self.private, user=self.a)

    def test_exports_deny_private_and_draft_without_access(self):
        for format in ('pdf', 'mp3'):
            for book in (self.private, self.draft):
                self.assertEqual(self.client.get(reverse('book-export', args=[book.slug, format])).status_code, 404)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(reverse('book-export', args=[self.private.slug, 'pdf'])).status_code, 404)

    @override_settings(SITE_URL='https://books.example.test')
    def test_pdf_contains_cyrillic_all_text_final_project_link_and_no_cache(self):
        from pypdf import PdfReader
        self.client.force_login(self.a)
        response = self.client.get(reverse('book-export', args=[self.private.slug, 'pdf']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('attachment', response['Content-Disposition'])
        pdf = PdfReader(BytesIO(b''.join(response.streaming_content)))
        response.close()
        self.assertEqual(len(pdf.pages), 3)
        self.assertIn('Секретная книга', pdf.pages[0].extract_text())
        self.assertIn('Секретный текст', pdf.pages[1].extract_text())
        self.assertIn('https://books.example.test/', pdf.pages[-1].extract_text())
        fonts = pdf.pages[1]['/Resources']['/Font'].get_object()
        self.assertTrue(any('/FontFile2' in font.get_object().get('/FontDescriptor', {}) for font in fonts.values()))

    def test_pdf_long_text_fits_exactly_one_leaf_with_large_picture(self):
        from pypdf import PdfReader
        from .exports import book_pdf
        self.page.text = ('Длинная история ребёнка. ' * 65) + 'Последняя строка истории.'
        output = book_pdf(self.private, [self.page])
        pdf = PdfReader(output)
        self.assertEqual(len(pdf.pages), 3)
        self.assertIn('Последняя строка истории.', pdf.pages[1].extract_text())
        from pypdf.generic import ContentStream
        matrices = [operands for operands, operator in ContentStream(pdf.pages[1].get_contents(), pdf).operations if operator == b'cm']
        self.assertTrue(any(float(matrix[0]) > 300 and float(matrix[3]) > 300 for matrix in matrices))
        output.close()

    def test_pdf_excessive_text_returns_error_without_extra_pages_or_truncation(self):
        from .exports import book_pdf, ExportError
        self.page.text = 'Длинная история ребёнка. ' * 1000
        with self.assertRaisesMessage(ExportError, 'слишком много текста'):
            book_pdf(self.private, [self.page])

    def test_pdf_every_source_page_owns_exactly_one_leaf(self):
        from .exports import book_pdf
        from pypdf import PdfReader
        pages = [self.page]
        for number in range(2, 6):
            pages.append(BookPage.objects.create(book=self.private, position=number, title=f'История {number}',
                         text=(f'Текст истории {number}. ' * (number*15)) + f'Конец истории {number}.', illustration=image_file()))
        output = book_pdf(self.private, pages)
        pdf = PdfReader(output)
        self.assertEqual(len(pdf.pages), len(pages)+2)
        for number in range(2, 6):
            self.assertIn(f'Конец истории {number}.', ' '.join(pdf.pages[number].extract_text().split()))
        output.close()

    def test_export_access_revocation_and_invalid_format(self):
        self.client.force_login(self.a)
        BookAccess.objects.filter(book=self.private).delete()
        self.assertEqual(self.client.get(reverse('book-export', args=[self.private.slug, 'pdf'])).status_code, 404)
        self.assertEqual(self.client.get(reverse('book-export', args=[self.public.slug, 'zip'])).status_code, 404)

    def test_mp3_without_recordings_returns_clear_error(self):
        response = self.client.get(reverse('book-export', args=[self.public.slug, 'mp3']))
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'нет аудиозаписей', status_code=422)

    def test_mp3_real_merge_of_different_mp3_and_wav_in_page_order(self):
        import imageio_ffmpeg
        from mutagen.mp3 import MP3
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'first.mp3'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-ar', '22050',
                            '-c:a', 'libmp3lame', str(first)], check=True, capture_output=True)
            BookPage.objects.create(book=self.public, position=2, title='Вторая', audio=wav_file())
            page = self.public.pages.get(position=1)
            page.audio.save('first.mp3', SimpleUploadedFile('first.mp3', first.read_bytes()))
            BookPage.objects.create(book=self.public, position=3, title='Без записи')
            response = self.client.get(reverse('book-export', args=[self.public.slug, 'mp3']))
            self.assertEqual(response.status_code, 200)
            content = b''.join(response.streaming_content)
            response.close()
            info = MP3(BytesIO(content)).info
            self.assertAlmostEqual(info.length, 2, delta=0.2)
            self.assertEqual(info.sample_rate, 44100)
            joined = Path(directory) / 'joined.mp3'
            joined.write_bytes(content)
            decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-loglevel', 'error',
                                      '-i', str(joined), '-f', 's16le', '-ac', '1', '-ar', '8000', '-'],
                                     check=True, capture_output=True).stdout
            import array
            samples = array.array('h', decoded)
            self.assertGreater(sum(abs(x) for x in samples[1600:6400]), 100000)
            self.assertLess(sum(abs(x) for x in samples[10000:14000]), 10000)

    def test_reader_shows_exports_auto_reader_and_missing_audio_count(self):
        response = self.client.get(reverse('reader', args=[self.public.slug]))
        self.assertContains(response, 'Скачать книгу PDF')
        self.assertContains(response, 'Страница без аудио:')
        self.assertContains(response, 'Только страница')
        self.assertContains(response, 'Вернуть меню')
        self.assertNotContains(response, 'Скачать аудиокнигу MP3')

    def test_export_busy_returns_retry_after(self):
        with patch('library.exports.export_slots.acquire', return_value=False):
            response = self.client.get(reverse('book-export', args=[self.public.slug, 'pdf']))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '10')
    def order_data(self):
        return {'parent_name': 'Родитель', 'email': 'parent@example.com', 'child_name': 'Дима', 'child_age': '3', 'child_description': 'Любит строить', 'event': 'Первый поход', 'wishes': '', 'consent': 'on'}
    def submit(self, **extra):
        self.client.get(reverse('order'))
        data = self.order_data() | {'submission_key': self.client.session['order_tokens'][-1]} | extra
        return self.client.post(reverse('order'), data), data
    def test_public_catalog_does_not_leak_private_or_draft(self):
        response = self.client.get(reverse('catalog'))
        self.assertContains(response, 'Публичная книга')
        self.assertNotContains(response, 'Секретная книга')
        self.assertNotContains(response, 'Черновик')
    def test_guest_private_reader_denied(self):
        self.assertEqual(self.client.get(reverse('reader', args=['private'])).status_code, 404)
    def test_other_user_private_reader_denied(self):
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(reverse('reader', args=['private'])).status_code, 404)
    def test_granted_reader_can_read(self):
        self.client.force_login(self.a)
        response = self.client.get(reverse('reader', args=['private']))
        self.assertContains(response, 'Секретный текст')
        self.assertContains(response, '<audio')
        self.assertNotContains(response, 'speechSynthesis')
        self.assertNotContains(response, 'Сохранить запись')
    def test_draft_denied_even_with_grant(self):
        BookAccess.objects.create(book=self.draft, user=self.a)
        self.client.force_login(self.a)
        self.assertEqual(self.client.get(reverse('reader', args=['draft'])).status_code, 404)
    def test_files_protected_for_guests_and_other_users(self):
        for field in [self.page.audio, self.page.illustration]:
            self.assertEqual(self.client.get(field.url).status_code, 404)
            self.client.force_login(self.b)
            self.assertEqual(self.client.get(field.url).status_code, 404)
            self.client.logout()
    def test_audio_range_and_head(self):
        self.client.force_login(self.a)
        response = self.client.get(self.page.audio.url, HTTP_RANGE='bytes=0-9')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(b''.join(response.streaming_content)), 10)
        self.assertTrue(response['Content-Range'].startswith('bytes 0-9/'))
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        head = self.client.head(self.page.audio.url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head['Content-Type'], 'audio/wav')
        self.assertEqual(head.content, b'')
    def test_invalid_range(self):
        self.client.force_login(self.a)
        for header in ['bytes=999999-1000000', 'bytes=5-2', 'bytes=-0', 'bytes=0-1,5-8', 'invalid']:
            self.assertEqual(self.client.get(self.page.audio.url, HTTP_RANGE=header).status_code, 416)
    def test_revocation_blocks_next_file_request(self):
        self.client.force_login(self.a)
        BookAccess.objects.filter(book=self.private, user=self.a).delete()
        self.assertEqual(self.client.get(self.page.audio.url).status_code, 404)
        self.assertEqual(self.client.get(reverse('reader', args=['private'])).status_code, 404)
    def test_account_shows_only_granted_books(self):
        self.client.force_login(self.a)
        response = self.client.get(reverse('account'))
        self.assertContains(response, 'Секретная книга')
        self.assertNotContains(response, 'Публичная книга')
    def test_registered_user_has_no_automatic_grants(self):
        data = {'username': 'new-reader', 'email': 'new@example.com', 'password1': 'LongPassword-fresh-829', 'password2': 'LongPassword-fresh-829'}
        self.assertEqual(self.client.post(reverse('register'), data).status_code, 302)
        self.assertEqual(BookAccess.objects.filter(user__username='new-reader').count(), 0)
        self.assertEqual(self.client.get(reverse('reader', args=['private'])).status_code, 404)
    def test_order_without_photo_and_duplicate_submit(self):
        recipient = NotificationRecipient.objects.create(name='Manager', channel='email', destination='staff@example.com')
        response, data = self.submit()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.client.post(reverse('order'), data)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertFalse(Order.objects.first().photo)
    def test_notification_creation_is_atomic_with_order(self):
        NotificationRecipient.objects.create(name='Manager', channel='email', destination='staff@example.com')
        with patch('library.views.Notification.objects.create', side_effect=RuntimeError('test rollback')):
            with self.assertRaises(RuntimeError): self.submit()
        self.assertEqual(Order.objects.count(), 0)
    def test_invalid_submission_key(self):
        response = self.client.post(reverse('order'), self.order_data() | {'submission_key': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
    def test_consent_required_and_photo_content_checked(self):
        response, _ = self.submit(consent='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        response, _ = self.submit(photo=SimpleUploadedFile('child.jpg', b'<script>alert(1)</script>'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
    def test_order_photo_only_staff(self):
        self.client.force_login(self.a)
        self.submit(photo=image_file())
        order = Order.objects.first()
        self.assertEqual(self.client.get(order.photo.url).status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get(order.photo.url)
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
    def test_page_audio_upload_editor_and_delete(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('page-audio', args=[self.page.pk]), {'audio': wav_file()})
        self.assertEqual(response.status_code, 302)
        self.page.refresh_from_db()
        self.assertTrue(self.page.audio.name.endswith('.wav'))
        self.client.post(reverse('page-audio', args=[self.page.pk]), {'action': 'remove'})
        self.page.refresh_from_db()
        self.assertFalse(self.page.audio)
    def test_reader_cannot_upload_audio(self):
        self.client.force_login(self.a)
        self.assertEqual(self.client.post(reverse('page-audio', args=[self.page.pk]), {'audio': wav_file()}).status_code, 404)
    def test_fake_audio_rejected(self):
        original = self.page.audio.name
        self.client.force_login(self.staff)
        self.client.post(reverse('page-audio', args=[self.page.pk]), {'audio': SimpleUploadedFile('fake.mp3', b'hello')})
        self.page.refresh_from_db()
        self.assertEqual(self.page.audio.name, original)
    def test_html_in_text_escaped(self):
        self.page.text = '<script>alert(1)</script>'
        self.page.save()
        self.client.force_login(self.a)
        response = self.client.get(reverse('reader', args=['private']))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert')
    def test_guest_orders_not_visible_to_arbitrary_registered_user(self):
        self.submit()
        self.a.email = 'parent@example.com'; self.a.save()
        self.client.force_login(self.a)
        self.assertNotContains(self.client.get(reverse('account')), str(Order.objects.first().id))
    def test_email_failure_keeps_order_and_retries(self):
        NotificationRecipient.objects.create(name='Manager', channel='email', destination='staff@example.com')
        self.submit()
        with patch('library.notifications.send_mail', side_effect=OSError('contains-private-token')):
            self.assertTrue(deliver_one())
        row = Notification.objects.get()
        self.assertEqual(row.state, 'pending')
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.last_error, 'OSError')
        self.assertEqual(Order.objects.count(), 1)
        row.next_attempt = timezone.now(); row.save()
        with patch('library.notifications.send_mail', return_value=1) as mail:
            deliver_one()
            self.assertNotIn('Дима', mail.call_args.args[1])
        row.refresh_from_db()
        self.assertEqual(row.state, 'sent')
    def test_retry_exhaustion_visible_as_failed(self):
        NotificationRecipient.objects.create(name='Manager', channel='email', destination='staff@example.com')
        self.submit()
        row = Notification.objects.get(); row.attempts = 7; row.save()
        with patch('library.notifications.send_mail', side_effect=OSError): deliver_one()
        row.refresh_from_db()
        self.assertEqual(row.state, 'failed')
    def test_revoked_telegram_recipient_cancelled(self):
        recipient = NotificationRecipient.objects.create(name='Editor', channel='telegram', destination='12345', employee=self.staff)
        self.submit()
        recipient.active = False; recipient.save()
        with patch('library.notifications.telegram_call') as api:
            deliver_one(); api.assert_not_called()
        self.assertEqual(Notification.objects.get().state, 'cancelled')
    def test_bot_unauthorized_has_no_order_information(self):
        self.submit()
        with patch('library.notifications.telegram_call') as api:
            handle_bot_update({'message': {'from': {'id': 999}, 'chat': {'type': 'private'}, 'text': '/orders'}})
            text = api.call_args.args[1]['text']
            self.assertNotIn(str(Order.objects.first().id), text)
            self.assertNotIn('/admin/', text)
    def test_bot_authorized_returns_protected_links(self):
        NotificationRecipient.objects.create(name='Editor', channel='telegram', destination='12345', employee=self.staff)
        self.submit()
        with patch('library.notifications.telegram_call') as api:
            handle_bot_update({'message': {'from': {'id': 12345}, 'chat': {'type': 'private'}, 'text': '/orders'}})
            self.assertIn('/admin/library/order/', api.call_args.args[1]['text'])
            self.assertNotIn('Дима', api.call_args.args[1]['text'])
    def test_csrf_required_for_order(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('order'), self.order_data()).status_code, 403)
    def test_pagination_and_invalid_reader_page(self):
        response = self.client.get(reverse('reader', args=['public']) + '?page=bad')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Открытая страница')

@override_settings(STATIC_ROOT=None, RATE_LIMIT_ENABLED=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'library.storage.PrivateStorage'}})
class RateLimitTests(TestCase):
    def test_repeated_login_is_limited(self):
        for _ in range(20): self.client.post(reverse('login'), {'username': 'none', 'password': 'bad'})
        self.assertEqual(self.client.post(reverse('login'), {}).status_code, 429)
