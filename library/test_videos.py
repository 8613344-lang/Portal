import io
import tempfile
import wave
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Book, BookAccess, BookPage, BookVideo
from .videos import current_video, fingerprint, process_one, queue_video, render_groups


@override_settings(RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND':'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class VideoTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.directory.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.public = Book.objects.create(title='Видео пример', slug='video-public', visibility='public', status='published')
        self.private = Book.objects.create(title='Личная книга', slug='video-private', status='published')
        self.draft = Book.objects.create(title='Черновик', slug='video-draft', visibility='public')
        self.user = User.objects.create_user('video-user')
        self.other = User.objects.create_user('other-video-user')
        self.admin = User.objects.create_superuser('video-admin', 'video-admin@example.com', 'test-password')
        self.grant = BookAccess.objects.create(book=self.private, user=self.user)

    def url(self, name, book=None):
        return reverse(name, args=[(book or self.public).slug])

    def mark_ready(self, book=None):
        book = book or self.public
        job = queue_video(book, 3)
        job.file.save('fixture.mp4', ContentFile(b'\x00\x00\x00\x18ftypisom' + b'a'*100), save=False)
        job.state = 'ready'
        job.save()
        return job

    def test_access_modes_do_not_expose_cached_private_video(self):
        self.mark_ready(self.private)
        for name in ('book-video', 'book-video-status', 'book-video-file'):
            self.assertEqual(self.client.get(self.url(name, self.private)).status_code, 404)
            self.assertEqual(self.client.get(self.url(name, self.draft)).status_code, 404)
        self.assertEqual(self.client.post(self.url('book-video-create', self.private)).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url('book-video-file', self.private)).status_code, 200)
        self.grant.delete()
        self.assertEqual(self.client.get(self.url('book-video-file', self.private), HTTP_RANGE='bytes=0-11').status_code, 404)

    def test_queue_requires_post_csrf_valid_delay_and_reuses_same_job(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.get(self.url('book-video-create')).status_code, 405)
        self.assertEqual(strict.post(self.url('book-video-create'), {'delay':3}).status_code, 403)
        strict.force_login(self.admin)
        strict.get(self.url('book-video'))
        token = strict.cookies['csrftoken'].value
        data = {'delay':3, 'csrfmiddlewaretoken':token}
        self.assertEqual(strict.post(self.url('book-video-create'), data).status_code, 202)
        original = BookVideo.objects.get(book=self.public)
        self.assertEqual(strict.post(self.url('book-video-create'), data).status_code, 202)
        self.assertEqual(BookVideo.objects.count(), 1)
        self.assertEqual(BookVideo.objects.get().pk, original.pk)
        self.client.force_login(self.admin)
        for value in ('2', '301', 'bad'):
            self.assertEqual(self.client.post(self.url('book-video-create'), {'delay':value}).status_code, 400)

    def test_public_video_creation_requires_staff_and_book_change_permission(self):
        staff = User.objects.create_user('video-staff', is_staff=True)
        nonstaff_editor = User.objects.create_user('video-nonstaff-editor')
        nonstaff_editor.user_permissions.add(Permission.objects.get(codename='change_book'))
        BookAccess.objects.create(book=self.public, user=self.user)
        for account in (None, self.user, staff, nonstaff_editor):
            client = Client()
            if account:
                client.force_login(account)
            with self.subTest(account=account), patch('library.video_views.queue_video') as queue:
                for query in ({}, {'app':1, 'device':'tablet'}):
                    response = client.get(self.url('book-video'), query)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, 'video-create')
                    self.assertNotContains(response, 'name="delay"')
                    self.assertContains(response, '← Читать книгу')
                    self.assertContains(response, 'data-pdf-url="/books/video-public/download/pdf/"')
                response = client.post(self.url('book-video-create'), {'delay':3})
                self.assertEqual(response.status_code, 403)
                queue.assert_not_called()
                self.assertFalse(BookVideo.objects.filter(book=self.public).exists())

    def test_readers_cannot_rebuild_or_change_delay_of_public_video(self):
        self.mark_ready()
        original = BookVideo.objects.filter(book=self.public).values().get()
        for account in (None, self.user):
            client = Client()
            if account:
                client.force_login(account)
            with self.subTest(account=account):
                self.assertEqual(client.post(self.url('book-video-create'), {'delay':300}).status_code, 403)
                self.assertEqual(BookVideo.objects.filter(book=self.public).values().get(), original)
                self.assertEqual(client.get(self.url('book-video-status')).json()['state'], 'ready')
                self.assertContains(client.get(self.url('book-video')), 'Скачать MP4')
                response = client.get(self.url('book-video-file'), HTTP_RANGE='bytes=0-11')
                self.assertEqual(response.status_code, 206)
                self.assertEqual(b''.join(response.streaming_content)[4:8], b'ftyp')

    def test_shared_links_do_not_grant_public_video_creation(self):
        for route, token in (('shared-reader', self.public.share_token),
                             ('shared-video', self.public.video_share_token)):
            client = Client()
            with self.subTest(route=route), patch('library.video_views.queue_video') as queue:
                self.assertEqual(client.get(reverse(route, args=[token])).status_code, 200)
                self.assertEqual(client.post(self.url('book-video-create'), {'delay':3}).status_code, 403)
                queue.assert_not_called()
        self.assertFalse(BookVideo.objects.exists())

    def test_administrators_can_create_and_rebuild_public_video(self):
        editor = User.objects.create_user('video-editor', is_staff=True)
        editor.user_permissions.add(Permission.objects.get(codename='change_book'))
        for account in (self.admin, editor):
            self.client.force_login(account)
            with self.subTest(account=account):
                self.assertContains(self.client.get(self.url('book-video'), {'app':1}), 'video-create')
                self.assertEqual(self.client.post(self.url('book-video-create'), {'delay':3}).status_code, 202)
                self.mark_ready()
                self.assertEqual(self.client.post(self.url('book-video-create'), {'delay':4}).status_code, 202)
                job = BookVideo.objects.get(book=self.public)
                self.assertEqual(job.delay, 4)
                self.assertEqual(job.state, 'queued')

    def test_assigned_private_book_reader_can_still_create_video(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url('book-video', self.private))
        self.assertContains(response, 'video-create')
        self.assertEqual(self.client.post(self.url('book-video-create', self.private), {'delay':3}).status_code, 202)
        self.assertTrue(BookVideo.objects.filter(book=self.private, state='queued').exists())

    def test_ranges_head_download_and_stale_cache(self):
        job = self.mark_ready()
        response = self.client.get(self.url('book-video-file'), HTTP_RANGE='bytes=0-11')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content)[4:8], b'ftyp')
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertIn('private', response['Cache-Control'])
        self.assertEqual(self.client.head(self.url('book-video-file'))['Content-Length'], str(job.file.size))
        self.assertIn('attachment', self.client.get(self.url('book-video-file'), {'download':1})['Content-Disposition'])
        self.public.title = 'Изменённая книга'
        self.public.save()
        self.assertEqual(self.client.get(self.url('book-video-status')).json()['state'], 'stale')
        self.assertEqual(self.client.get(self.url('book-video-file')).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url('book-video-create'), {'delay':3}).status_code, 202)
        job.refresh_from_db()
        self.assertEqual(job.state, 'queued')

    def test_shared_token_allows_only_its_book_and_rotation_revokes_video(self):
        self.mark_ready(self.private)
        self.client.get(reverse('shared-reader', args=[self.private.share_token]))
        self.assertEqual(self.client.get(self.url('book-video', self.private)).status_code, 200)
        self.assertEqual(self.client.get(self.url('book-video-file', self.private)).status_code, 200)
        import uuid
        self.private.share_token = uuid.uuid4()
        self.private.save()
        self.assertEqual(self.client.get(self.url('book-video-file', self.private)).status_code, 404)

    def test_app_metadata_and_video_layout_keep_safe_account_context(self):
        reader = self.client.get(self.url('reader'), {'app':1, 'device':'tablet'})
        self.assertContains(reader, 'data-pdf-url="/books/video-public/download/pdf/"')
        self.assertContains(reader, 'data-video-url="/books/video-public/video/"')
        video = self.client.get(self.url('book-video'), {'app':1})
        self.assertTemplateUsed(video, 'app_base.html')
        self.assertContains(video, 'playsinline')
        self.assertEqual(self.client.get(reverse('app-context')).json()['account'], 'guest')
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('app-context')).json()['account'], str(self.user.pk))

    def test_imported_spreads_and_generated_alternating_pages_keep_audio_order(self):
        self.public.pdf_layout = 'alternating'
        a = BookPage.objects.create(book=self.public, position=1, title='Картинка', illustration='one.jpg', pdf_full_page=True, audio='one.mp3')
        b = BookPage.objects.create(book=self.public, position=2, title='Текст', illustration='two.jpg', pdf_full_page=True, audio='two.wav')
        c = BookPage.objects.create(book=self.public, position=3, title='Глава', illustration='three.jpg', text='Текст')
        groups, count = render_groups(self.public, [a,b,c])
        self.assertEqual(count, 6)  # cover + 2 facsimiles + image/text + ending
        self.assertEqual(groups[1][0], [1,2])
        self.assertEqual([field.name for field in groups[1][1]], ['one.mp3','two.wav'])
        self.assertEqual(groups[2][0], [3,4])

    def test_expired_worker_lease_is_recovered_and_failure_can_retry(self):
        job = queue_video(self.public, 3)
        BookVideo.objects.filter(pk=job.pk).update(state='rendering', lease_until=timezone.now()-timedelta(seconds=1))
        from .exports import ExportError
        with patch('library.videos.render_mp4', side_effect=ExportError('Ошибка тестовой записи')):
            self.assertTrue(process_one())
        job.refresh_from_db()
        self.assertEqual(job.state, 'failed')
        self.assertEqual(job.error, 'Ошибка тестовой записи')
        queue_video(self.public, 3)
        job.refresh_from_db()
        self.assertEqual(job.state, 'queued')

    def test_actual_mp4_with_audio_is_saved_and_second_request_reuses_file(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as recording:
            recording.setnchannels(1); recording.setsampwidth(2); recording.setframerate(48000)
            recording.writeframes(b'\0\0'*12000)
        self.public.cover_audio.save('cover.wav', ContentFile(buffer.getvalue()), save=True)
        job = queue_video(self.public, 3)
        self.assertTrue(process_one())
        job.refresh_from_db()
        self.assertEqual(job.state, 'ready', job.error)
        self.assertEqual(job.progress, job.total)
        with job.file.open('rb') as stream:
            self.assertEqual(stream.read(8)[4:8], b'ftyp')
        import imageio_ffmpeg
        frames, seconds = imageio_ffmpeg.count_frames_and_secs(job.file.path)
        self.assertGreater(frames, 60)
        self.assertTrue(3.0 < seconds < 4.0, seconds)
        name = job.file.name
        self.client.force_login(self.admin)
        with patch('library.videos.render_mp4') as renderer:
            response = self.client.post(self.url('book-video-create'), {'delay':3})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(process_one())
            renderer.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.file.name, name)

    def test_new_request_cancels_old_worker_without_publishing_obsolete_file(self):
        job = queue_video(self.public, 3)
        def obsolete(book, delay, root, progress):
            queue_video(book, 4)
            result = root / 'cancelled.mp4'
            result.write_bytes(b'obsolete')
            return result
        with patch('library.videos.render_mp4', side_effect=obsolete):
            self.assertTrue(process_one())
        job.refresh_from_db()
        self.assertEqual(job.state, 'queued')
        self.assertEqual(job.delay, 4)
        self.assertFalse(job.file)

    def test_mp3_and_wav_recordings_are_joined_into_a_real_video(self):
        import imageio_ffmpeg, subprocess
        root = Path(self.directory.name)
        with wave.open(str(root / 'record.wav'), 'wb') as recording:
            recording.setnchannels(1); recording.setsampwidth(2); recording.setframerate(48000)
            recording.writeframes(b'\0\0'*24000)
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-loglevel','error',
                        '-i','record.wav','-c:a','libmp3lame','record.mp3'],
                       cwd=root, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW',0))
        self.public.cover_audio.save('cover.wav', ContentFile((root/'record.wav').read_bytes()), save=False)
        self.public.ending_audio.save('ending.mp3', ContentFile((root/'record.mp3').read_bytes()), save=True)
        job = queue_video(self.public, 3)
        self.assertTrue(process_one())
        job.refresh_from_db()
        self.assertEqual(job.state,'ready',job.error)
        frames, duration = imageio_ffmpeg.count_frames_and_secs(job.file.path)
        self.assertGreater(frames, 15)
        self.assertTrue(.9 < duration < 1.5, duration)
