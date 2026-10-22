import hashlib
import io
import tempfile
from datetime import timedelta
from functools import lru_cache
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .analytics import agent_info, client_ip
from .images import image_url
from .models import Book, BookAccess, BookPage, HomepageItem, PageVisit


@lru_cache(maxsize=1)
def large_picture():
    output = io.BytesIO()
    Image.effect_noise((1200, 1600), 35).convert('RGB').save(output, 'JPEG', quality=93)
    return output.getvalue()


@override_settings(RATE_LIMIT_ENABLED=False, STATIC_ROOT=None, BEHIND_PROXY=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default': {'BACKEND':'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class SiteImprovementTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        override = override_settings(MEDIA_ROOT=folder.name)
        override.enable(); self.addCleanup(override.disable)
        self.public = Book.objects.create(title='Открытый пример', slug='improve-public', status='published', visibility='public')
        self.private = Book.objects.create(title='Скрытая книга', slug='improve-private', status='published')
        self.reader = User.objects.create_user('improve-reader')
        self.staff = User.objects.create_superuser('improve-editor','editor@example.test','test')

    def cover(self, book=None):
        book = book or self.public
        book.cover.save('source.jpg', ContentFile(large_picture()), save=True)
        return book.cover

    def test_webp_is_resized_lighter_and_does_not_change_export_source(self):
        field = self.cover()
        original = hashlib.sha256(field.read()).hexdigest(); field.close()
        response = self.client.get(image_url(field, 640))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')
        data = b''.join(response.streaming_content); response.close()
        self.assertLess(len(data), len(large_picture())//2)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (640, 853))
        with field.open('rb') as source:
            self.assertEqual(hashlib.sha256(source.read()).hexdigest(), original)
        self.assertNotIn('exif', Image.open(io.BytesIO(data)).info)

    def test_derivative_cache_is_reused_and_etag_revalidated(self):
        field = self.cover()
        url = image_url(field, 320)
        first = self.client.get(url); tag = first['ETag']; first.close()
        with patch('library.images.Image.open', side_effect=AssertionError('must use disk cache')):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200); response.close()
            self.assertEqual(self.client.get(url, HTTP_IF_NONE_MATCH=tag).status_code, 304)
        self.assertIn('private', response['Cache-Control'])

    def test_cached_image_and_304_do_not_bypass_revoked_access(self):
        field = self.cover(self.private)
        grant = BookAccess.objects.create(book=self.private, user=self.reader)
        url = image_url(field)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.reader)
        response = self.client.get(url); tag = response['ETag']; response.close()
        grant.delete()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, HTTP_IF_NONE_MATCH=tag).status_code, 404)

    def test_book_link_grants_images_but_video_link_does_not(self):
        field = self.cover(self.private)
        self.client.get(reverse('shared-video', args=[self.private.video_share_token]))
        self.assertEqual(self.client.get(image_url(field)).status_code, 404)
        self.client.get(reverse('shared-reader', args=[self.private.share_token]))
        response = self.client.get(image_url(field)); self.assertEqual(response.status_code, 200); response.close()

    def test_invalid_image_sizes_paths_and_audio_are_rejected(self):
        field = self.cover()
        for path in (f'/web-image/123/{field.name}', '/web-image/640/book/file.mp3', '/web-image/640/not-a-file.jpg'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_changed_source_has_new_etag_and_no_upscaling(self):
        field = self.cover()
        response = self.client.get(image_url(field)); tag = response['ETag']; response.close()
        output = io.BytesIO(); Image.new('RGB', (80, 120), 'red').save(output,'PNG')
        self.public.cover.save('replacement.png', ContentFile(output.getvalue()), save=True)
        response = self.client.get(image_url(self.public.cover), HTTP_IF_NONE_MATCH=tag)
        self.assertEqual(response.status_code, 200); self.assertNotEqual(response['ETag'], tag)
        self.assertEqual(Image.open(io.BytesIO(b''.join(response.streaming_content))).size, (80,120)); response.close()

    def test_showcase_order_visibility_and_public_book_guard(self):
        HomepageItem.objects.all().delete()
        HomepageItem.objects.create(kind='book', book=self.public, position=3)
        HomepageItem.objects.create(title='Моя выбранная идея', kind='idea', position=1)
        HomepageItem.objects.create(title='Выключенная идея', enabled=False, position=2)
        HomepageItem.objects.create(kind='book', book=self.private, position=0)  # Simulate stale/malicious DB state.
        response = self.client.get(reverse('home'))
        self.assertEqual([item.position for item in response.context['showcase']], [1,3])
        self.assertContains(response, 'Моя выбранная идея')
        self.assertNotContains(response, self.private.title)
        self.assertNotContains(response, 'Выключенная идея')
        self.public.visibility = 'restricted'; self.public.save()
        self.assertNotContains(self.client.get(reverse('home')), self.public.title)

    def test_showcase_validation_and_idea_image_visibility(self):
        with self.assertRaises(ValidationError): HomepageItem(kind='book', book=self.private).full_clean()
        with self.assertRaises(ValidationError): HomepageItem(kind='idea', title='').full_clean()
        item = HomepageItem.objects.create(title='Идея с обложкой')
        item.image.save('idea.jpg', ContentFile(large_picture()), save=True)
        response = self.client.get(image_url(item.image)); self.assertEqual(response.status_code,200); response.close()
        item.enabled = False; item.save()
        self.assertEqual(self.client.get(image_url(item.image)).status_code, 404)
        self.client.force_login(self.staff)
        response = self.client.get(image_url(item.image)); self.assertEqual(response.status_code,200); response.close()

    def test_staff_can_configure_homepage_without_touching_books(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('admin:library_homepageitem_add'), {
            'kind':'idea', 'title':'Идея из админки', 'description':'Текст идеи',
            'default_image':'space', 'theme':'blue', 'position':1, 'enabled':'on', '_save':'Сохранить'})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(reverse('home')), 'Идея из админки')
        self.private.refresh_from_db(); self.assertEqual(self.private.visibility,'restricted')

    def test_visit_records_browser_user_ip_and_sanitizes_sources(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse('home')+'?email=secret@example.test',
            REMOTE_ADDR='203.0.113.15', HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/145.0.2.1 Safari/537.36',
            HTTP_REFERER='https://source.example:443/read/secret-token/?email=private')
        self.assertEqual(response.status_code, 200)
        row = PageVisit.objects.latest('pk')
        self.assertEqual((row.user_id,row.ip_address,row.browser,row.browser_version,row.os),
                         (self.reader.pk,'203.0.113.15','Chrome','145.0.2.1','Windows'))
        self.assertEqual(row.path,'/'); self.assertEqual(row.referrer,'https://source.example:443')
        self.assertEqual(len(row.visitor_key),64)
        self.assertNotIn('secret', row.referrer)

    def test_book_open_and_ajax_turn_are_separate_without_bearer_urls(self):
        BookPage.objects.create(book=self.private, position=1, title='Страница')
        self.client.get(reverse('shared-reader', args=[self.private.share_token]))
        self.client.get(reverse('shared-reader', args=[self.private.share_token]), {'page':2}, HTTP_X_REQUESTED_WITH='book-reader')
        rows = list(PageVisit.objects.order_by('pk'))
        self.assertEqual([(row.kind,row.book_page) for row in rows], [('book_open',1),('book_page',2)])
        self.assertTrue(all(row.path == reverse('reader',args=[self.private.slug]) for row in rows))
        self.assertNotIn(str(self.private.share_token), str(list(PageVisit.objects.values())))

    def test_assets_status_polling_head_posts_and_errors_are_not_visits(self):
        self.client.get(reverse('health'))
        self.client.get(reverse('book-video-status', args=[self.public.slug]))
        self.client.head(reverse('home'))
        self.client.post(reverse('order'), {})
        self.client.get('/not-found/')
        self.assertEqual(PageVisit.objects.count(),0)

    @override_settings(ANALYTICS_ENABLED=False)
    def test_analytics_can_be_disabled(self):
        self.client.get(reverse('home'))
        self.assertFalse(PageVisit.objects.exists())
        self.assertNotContains(self.client.get(reverse('privacy')), 'Техническая статистика сайта')

    def test_statistics_failure_does_not_break_the_site(self):
        with patch('library.analytics.PageVisit.objects.create', side_effect=DatabaseError('test')):
            with self.assertLogs('library.analytics', level='WARNING'):
                self.assertEqual(self.client.get(reverse('home')).status_code,200)
        self.assertEqual(PageVisit.objects.count(),0)

    def test_ip_trust_and_agent_parsing(self):
        from django.test import RequestFactory
        request = RequestFactory().get('/', REMOTE_ADDR='203.0.113.1', HTTP_X_REAL_IP='198.51.100.1')
        with override_settings(BEHIND_PROXY=True, ANALYTICS_TRUSTED_PROXIES=['127.0.0.1/32']):
            self.assertEqual(client_ip(request),'203.0.113.1')
            request.META['REMOTE_ADDR']='127.0.0.1'
            self.assertEqual(client_ip(request),'198.51.100.1')
        info = agent_info('Mozilla/5.0 (Android 12; Tablet) Chrome/145.0.0.0 Safari/537.36 EdgA/145.0.1')
        self.assertEqual(info[:4],('Edge','145.0.1','Android','Планшет'))
        self.assertTrue(agent_info('Googlebot/2.1')[4])

    def test_stats_dashboard_requires_model_permission_and_is_readonly(self):
        url = reverse('admin:library_pagevisit_dashboard')
        self.assertEqual(self.client.get(url).status_code,302)
        staff = User.objects.create_user('limited-stats',is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code,403)
        staff.user_permissions.add(Permission.objects.get(codename='view_pagevisit'))
        self.client.force_login(staff)
        PageVisit.objects.create(path='/', visitor_key='test', ip_address='203.0.113.15')
        self.assertEqual(self.client.get(url).status_code,200)
        journal = self.client.get(reverse('admin:library_pagevisit_changelist'))
        self.assertContains(journal, '203.0.113.15')
        row = PageVisit.objects.first()
        self.assertEqual(self.client.post(reverse('admin:library_pagevisit_change',args=[row.pk]),{'path':'/tampered/'}).status_code,403)

    def test_retention_and_account_deletion_remove_identified_visits(self):
        PageVisit.objects.create(path='/old/',visitor_key='old',occurred_at=timezone.now()-timedelta(days=91))
        PageVisit.objects.create(path='/',visitor_key='now',user=self.reader)
        call_command('run_analytics', once=True)
        self.assertEqual(PageVisit.objects.count(),1)
        self.reader.delete()
        self.assertEqual(PageVisit.objects.count(),0)
