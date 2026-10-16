import tempfile
import wave
from io import BytesIO

from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.forms import modelform_factory
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AudioPreview, Book, BookPage, HomepagePromotion


def preview_wav(name='preview.wav'):
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(b'\x00\x00' * 800)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='audio/wav')


@override_settings(
    RATE_LIMIT_ENABLED=False,
    STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class HomepageSettingsTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media.name)
        cls.media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.media_override.disable()
        cls.media.cleanup()

    def setUp(self):
        self.preview = AudioPreview.objects.get(position=1)
        self.promotion = HomepagePromotion.objects.get(pk=1)

    def staff_with_permissions(self, model, *codenames):
        user = User.objects.create_user(
            username='homepage-editor', email='editor@example.com', is_staff=True,
        )
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='library', content_type__model=model,
            codename__in=codenames,
        ))
        self.client.force_login(user)
        return user

    def save_recording(self, preview=None):
        preview = preview or self.preview
        preview.audio.save('preview.wav', preview_wav())
        return preview

    def test_migration_seeds_exactly_five_ordered_preview_slots(self):
        self.assertEqual(list(AudioPreview.objects.values_list('position', flat=True)), [1, 2, 3, 4, 5])
        self.assertEqual(
            list(AudioPreview.objects.values_list('title', flat=True)),
            [f'Вариант озвучки {number}' for number in range(1, 6)],
        )
        response = self.client.get(reverse('home'))
        self.assertEqual([item['position'] for item in response.context['audio_previews']], [1, 2, 3, 4, 5])
        self.assertTrue(all(not item['available'] and not item['url'] for item in response.context['audio_previews']))

    def test_configured_preview_uses_public_position_route_and_custom_title(self):
        self.save_recording()
        self.preview.title = 'Тёплый голос рассказчика'
        self.preview.save(update_fields=['title'])
        response = self.client.get(reverse('home'))
        item = response.context['audio_previews'][0]
        self.assertEqual(item['title'], 'Тёплый голос рассказчика')
        self.assertTrue(item['available'])
        self.assertEqual(item['url'], reverse('audio-preview', args=[1]))
        self.assertContains(response, 'Тёплый голос рассказчика')
        self.assertNotContains(response, self.preview.audio.name)

    def test_disabled_recording_has_no_available_url(self):
        self.save_recording()
        self.preview.enabled = False
        self.preview.save(update_fields=['enabled'])
        response = self.client.get(reverse('home'))
        item = response.context['audio_previews'][0]
        self.assertFalse(item['available'])
        self.assertEqual(item['url'], '')
        self.assertEqual(self.client.get(reverse('audio-preview', args=[1])).status_code, 404)
        self.assertEqual(self.client.head(reverse('audio-preview', args=[1])).status_code, 404)

    def test_book_audio_is_never_automatically_published_as_a_preview(self):
        for slug, status, visibility in (
            ('public', 'published', 'public'),
            ('private', 'published', 'restricted'),
            ('draft', 'draft', 'public'),
        ):
            book = Book.objects.create(
                title=slug, slug=slug, status=status, visibility=visibility,
                cover_audio=f'book/{slug}.mp3',
            )
            BookPage.objects.create(book=book, position=1, title='Глава', audio=f'bookpage/{slug}.wav')
        self.client.force_login(User.objects.create_superuser('editor', 'admin@example.com', 'test-password'))
        response = self.client.get(reverse('home'))
        self.assertEqual(len(response.context['audio_previews']), 5)
        self.assertTrue(all(not item['available'] and not item['url'] for item in response.context['audio_previews']))
        self.assertNotContains(response, 'book/public.mp3')
        self.assertNotContains(response, 'book/private.mp3')
        self.assertNotContains(response, 'bookpage/private.wav')

    def test_missing_settings_render_five_slots_and_default_promotion_without_public_get_writes(self):
        AudioPreview.objects.all().delete()
        HomepagePromotion.objects.all().delete()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        previews = response.context['audio_previews']
        self.assertEqual([item['position'] for item in previews], [1, 2, 3, 4, 5])
        self.assertTrue(all(not item['available'] for item in previews))
        self.assertContains(response, 'Хороший повод начать')
        self.assertContains(response, 'Скидка на создание книги сейчас!')
        self.assertEqual(AudioPreview.objects.count(), 0)
        self.assertEqual(HomepagePromotion.objects.count(), 0)

    def test_public_recording_supports_complete_get_head_and_byte_ranges(self):
        self.save_recording()
        with self.preview.audio.open('rb') as recording:
            expected = recording.read()
        url = reverse('audio-preview', args=[1])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'audio/wav')
        self.assertEqual(response['Content-Length'], str(len(expected)))
        self.assertEqual(b''.join(response.streaming_content), expected)
        response.close()
        response = self.client.get(url, HTTP_RANGE='bytes=4-13')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], f'bytes 4-13/{len(expected)}')
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(b''.join(response.streaming_content), expected[4:14])
        response.close()
        response = self.client.get(url, HTTP_RANGE='bytes=-10')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), expected[-10:])
        response.close()
        head = self.client.head(url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head['Content-Length'], str(len(expected)))
        self.assertEqual(head.content, b'')

    def test_invalid_audio_ranges_return_416(self):
        self.save_recording()
        url = reverse('audio-preview', args=[1])
        for value in ('bytes=999999-', 'bytes=5-2', 'bytes=-0', 'bytes=0-1,5-8', 'invalid'):
            with self.subTest(value=value):
                response = self.client.get(url, HTTP_RANGE=value)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response['Content-Range'], f'bytes */{self.preview.audio.size}')

    def test_empty_missing_and_out_of_range_preview_files_are_404(self):
        for position in (0, 1, 6):
            self.assertEqual(self.client.get(reverse('audio-preview', args=[position])).status_code, 404)
        self.save_recording()
        self.preview.audio.storage.delete(self.preview.audio.name)
        self.assertEqual(self.client.get(reverse('audio-preview', args=[1])).status_code, 404)

    def test_preview_upload_is_not_exposed_through_generic_private_file_route(self):
        self.save_recording()
        self.assertEqual(self.client.get(self.preview.audio.url).status_code, 404)
        self.preview.enabled = False
        self.preview.save(update_fields=['enabled'])
        self.assertEqual(self.client.get(self.preview.audio.url).status_code, 404)
        self.assertEqual(self.client.post(reverse('audio-preview', args=[1])).status_code, 405)

    def test_audio_editor_private_preview_link_does_not_unlock_book_files(self):
        self.save_recording()
        private_book = Book.objects.create(title='Чужая книга', slug='private-audio', visibility='restricted', status='published')
        private_book.cover_audio.save('private.wav', preview_wav())
        self.staff_with_permissions('audiopreview', 'change_audiopreview')
        response = self.client.get(self.preview.audio.url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(private_book.cover_audio.url).status_code, 404)
        self.preview.enabled = False
        self.preview.save(update_fields=['enabled'])
        self.assertEqual(self.client.get(reverse('audio-preview', args=[1])).status_code, 404)

    def test_audio_validation_accepts_wav_and_rejects_wrong_extension_or_contents(self):
        form_type = modelform_factory(AudioPreview, fields=['title', 'audio', 'enabled'])
        form = form_type({'title': 'Настоящая запись', 'enabled': 'on'}, {'audio': preview_wav()}, instance=self.preview)
        self.assertTrue(form.is_valid(), form.errors)
        for upload in (
            preview_wav('recording.ogg'),
            preview_wav('recording.mp3'),
            SimpleUploadedFile('recording.wav', b'not audio', content_type='audio/wav'),
        ):
            with self.subTest(filename=upload.name):
                form = form_type({'title': 'Ошибочная запись', 'enabled': 'on'}, {'audio': upload}, instance=self.preview)
                self.assertFalse(form.is_valid())
                self.assertIn('audio', form.errors)

    def test_staff_can_configure_audio_title_file_and_availability_in_admin(self):
        self.staff_with_permissions('audiopreview', 'change_audiopreview')
        url = reverse('admin:library_audiopreview_change', args=[1])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            'title': 'Голос для вечерней сказки', 'audio': preview_wav(), 'enabled': 'on', '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302)
        self.preview.refresh_from_db()
        self.assertEqual(self.preview.title, 'Голос для вечерней сказки')
        self.assertTrue(self.preview.audio)
        self.client.logout()
        self.assertTrue(self.client.get(reverse('home')).context['audio_previews'][0]['available'])

    def test_staff_without_audio_change_permission_cannot_modify_configuration(self):
        self.staff_with_permissions('audiopreview', 'view_audiopreview')
        url = reverse('admin:library_audiopreview_change', args=[1])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {'title': 'Чужое изменение', 'enabled': 'on'}).status_code, 403)
        self.preview.refresh_from_db()
        self.assertEqual(self.preview.title, 'Вариант озвучки 1')

    def test_staff_can_disable_and_clear_preview_without_leaving_public_file_access(self):
        self.save_recording()
        original_private_url = self.preview.audio.url
        self.staff_with_permissions('audiopreview', 'change_audiopreview')
        url = reverse('admin:library_audiopreview_change', args=[1])
        response = self.client.post(url, {'title': self.preview.title, '_save': 'Сохранить'})
        self.assertEqual(response.status_code, 302)
        self.preview.refresh_from_db()
        self.assertFalse(self.preview.enabled)
        self.assertEqual(self.client.get(reverse('audio-preview', args=[1])).status_code, 404)
        response = self.client.post(url, {
            'title': self.preview.title, 'enabled': 'on', 'audio-clear': 'on', '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302)
        self.preview.refresh_from_db()
        self.assertFalse(self.preview.audio)
        self.assertEqual(self.client.get(original_private_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('audio-preview', args=[1])).status_code, 404)

    def test_anonymous_and_nonstaff_cannot_modify_homepage_settings(self):
        for user in (None, User.objects.create_user('ordinary-reader')):
            if user:
                self.client.force_login(user)
            for model, data in (
                ('audiopreview', {'title': 'Посторонняя озвучка', 'enabled': 'on'}),
                ('homepagepromotion', {'eyebrow': 'Чужая надпись', 'title': 'Чужой заголовок', 'text': 'Чужой текст'}),
            ):
                response = self.client.post(reverse(f'admin:library_{model}_change', args=[1]), data)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('admin:login'), response.url)
        self.preview.refresh_from_db()
        self.promotion.refresh_from_db()
        self.assertEqual(self.preview.title, 'Вариант озвучки 1')
        self.assertNotEqual(self.promotion.title, 'Чужой заголовок')

    def test_seeded_preview_slots_and_promotion_cannot_be_added_or_deleted_in_admin(self):
        self.client.force_login(User.objects.create_superuser('editor', 'admin@example.com', 'test-password'))
        for model in ('audiopreview', 'homepagepromotion'):
            for action, args in (('add', []), ('delete', [1])):
                url = reverse(f'admin:library_{model}_{action}', args=args)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(AudioPreview.objects.count(), 5)
        self.assertEqual(HomepagePromotion.objects.count(), 1)

    def test_database_enforces_five_slot_positions_and_promotion_singleton(self):
        for position in (0, 6):
            with self.subTest(position=position):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    AudioPreview.objects.bulk_create([AudioPreview(position=position, title='Лишний вариант')])
        with self.assertRaises(IntegrityError), transaction.atomic():
            HomepagePromotion.objects.bulk_create([HomepagePromotion(pk=2, title='Лишний баннер')])

    def test_promotion_admin_updates_all_three_texts_on_home_immediately(self):
        self.staff_with_permissions('homepagepromotion', 'change_homepagepromotion')
        url = reverse('admin:library_homepagepromotion_change', args=[1])
        response = self.client.post(url, {
            'eyebrow': 'Для новой семейной традиции', 'title': 'Ваш особенный повод',
            'text': 'Расскажите нам о своей идее.\nУточним пожелания вместе.', '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302)
        self.promotion.refresh_from_db()
        self.assertEqual(self.promotion.eyebrow, 'Для новой семейной традиции')
        self.assertEqual(self.promotion.title, 'Ваш особенный повод')
        self.assertEqual(self.promotion.text, 'Расскажите нам о своей идее.\nУточним пожелания вместе.')
        self.assertIsNotNone(self.promotion.updated_at)
        self.client.logout()
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Для новой семейной традиции')
        self.assertContains(response, 'Ваш особенный повод')
        self.assertContains(response, 'Расскажите нам о своей идее.')

    def test_promotion_and_preview_texts_are_escaped(self):
        self.promotion.eyebrow = '<script>badEyebrow()</script>'
        self.promotion.title = '<script>badTitle()</script>'
        self.promotion.text = '<img src=x onerror=badText()>'
        self.promotion.save()
        self.preview.title = '<img src=x onerror=badAudio()>'
        self.preview.save(update_fields=['title'])
        response = self.client.get(reverse('home'))
        for text in ('<script>badEyebrow()</script>', '<script>badTitle()</script>', '<img src=x onerror=badText()>', '<img src=x onerror=badAudio()>'):
            self.assertNotContains(response, text)
        self.assertContains(response, '&lt;script&gt;badTitle()&lt;/script&gt;')
        self.assertContains(response, '&lt;img src=x onerror=badText()&gt;')
        self.assertContains(response, '&lt;img src=x onerror=badAudio()&gt;')

    def test_staff_without_promotion_permission_cannot_view_or_edit_promotion(self):
        self.staff_with_permissions('homepagepromotion')
        url = reverse('admin:library_homepagepromotion_change', args=[1])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'eyebrow': 'Постороннее', 'title': 'Изменение', 'text': 'Запрещено'}).status_code, 403)
        self.promotion.refresh_from_db()
        self.assertNotEqual(self.promotion.title, 'Изменение')
