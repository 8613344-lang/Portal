import tempfile
import uuid
import wave
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
        cls.media_override = override_settings(MEDIA_ROOT=cls.media.name)
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

@override_settings(RATE_LIMIT_ENABLED=True, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}, 'default': {'BACKEND': 'library.storage.PrivateStorage'}})
class RateLimitTests(TestCase):
    def test_repeated_login_is_limited(self):
        for _ in range(20): self.client.post(reverse('login'), {'username': 'none', 'password': 'bad'})
        self.assertEqual(self.client.post(reverse('login'), {}).status_code, 429)
