import csv
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Book, BookAccess, PageVisit, VideoPosition
from .videos import queue_video


@override_settings(RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={'default':{'BACKEND':'library.storage.PrivateStorage'},
              'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StatisticsAndProgressTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name, ANALYTICS_GEOIP_PATH=Path(directory.name)/'missing.mmdb')
        settings.enable(); self.addCleanup(settings.disable)
        self.book = Book.objects.create(title='Закрытая книга', slug='progress-private', status='published')
        self.user = User.objects.create_user('progress-reader', 'reader@example.com')
        self.other = User.objects.create_user('progress-other')
        BookAccess.objects.create(book=self.book, user=self.user)
        self.job = queue_video(self.book, 3)
        self.job.file.save('fixture.mp4', ContentFile(b'video'), save=False)
        self.job.state='ready'; self.job.save()
        self.url = reverse('book-video-position',args=[self.book.slug])
        self.data = {'account':str(self.user.pk), 'revision':self.job.updated_at.isoformat(),
            'position':25.5, 'duration':100, 'client_id':'test-tab', 'sequence':1}

    def post(self, data=None, client=None):
        return (client or self.client).post(self.url, data=json.dumps(data or self.data), content_type='application/json')

    def test_position_is_account_scoped_and_revoked_with_video_access(self):
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.client.get(self.url).json()['position'],25.5)
        second = Client(); second.force_login(self.user)
        self.assertEqual(second.get(self.url).json()['position'],25.5)
        BookAccess.objects.create(book=self.book,user=self.other)
        second.force_login(self.other)
        self.assertEqual(second.get(self.url).json()['position'],0)
        self.assertEqual(self.post({**self.data,'account':str(self.other.pk)}).status_code,403)
        self.book.grants.filter(user=self.user).delete()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.post().status_code,404)

    def test_guest_video_link_has_local_progress_only_and_no_book_source_access(self):
        self.client.get(reverse('shared-video',args=[self.book.video_share_token]))
        self.assertEqual(self.client.get(self.url).status_code,401)
        self.assertEqual(self.post().status_code,401)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.post({**self.data,'account':str(self.other.pk)}).status_code,200)
        self.assertEqual(self.client.get(reverse('reader',args=[self.book.slug])).status_code,404)

    def test_position_requires_csrf_valid_numbers_and_current_revision(self):
        self.client.force_login(self.user)
        for invalid in (-1,float('nan'),float('inf'),101,'bad'):
            self.assertEqual(self.post({**self.data,'position':invalid}).status_code,400)
        self.assertEqual(self.post({**self.data,'revision':'old'}).status_code,409)
        self.assertFalse(VideoPosition.objects.exists())
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.user)
        self.assertEqual(self.post(client=strict).status_code,403)
        strict.get(reverse('book-video',args=[self.book.slug]))
        self.assertEqual(strict.post(self.url,data=json.dumps(self.data),content_type='application/json',
            HTTP_X_CSRFTOKEN=strict.cookies['csrftoken'].value).status_code,200)
        self.assertEqual(self.client.put(self.url).status_code,405)

    def test_late_save_cannot_overwrite_newer_sample_and_regeneration_resets_resume(self):
        self.client.force_login(self.user)
        self.post({**self.data,'sequence':2,'position':40})
        self.post()
        self.assertEqual(self.client.get(self.url).json()['position'],40)
        self.post({**self.data,'sequence':3,'position':0})
        self.assertEqual(self.client.get(self.url).json()['position'],0)
        self.post({**self.data,'sequence':4,'position':45})
        self.job.save()
        self.assertEqual(self.client.get(self.url).json()['position'],0)
        self.assertEqual(self.post().status_code,409)
        self.user.delete()
        self.assertFalse(VideoPosition.objects.exists())

    def staff(self, permit=True):
        staff = User.objects.create_user('stats-csv',is_staff=True)
        if permit: staff.user_permissions.add(Permission.objects.get(codename='view_pagevisit'))
        self.client.force_login(staff)
        return staff

    def test_csv_exports_all_filtered_rows_with_city_bom_and_formula_protection(self):
        self.staff()
        PageVisit.objects.create(path='/match/',visitor_key='one',city='Москва',country='Россия',
            ip_address='8.8.8.8',user_agent='=HYPERLINK("bad")',user=self.user)
        PageVisit.objects.create(path='/other/',visitor_key='two')
        url=reverse('admin:library_pagevisit_csv')
        response=self.client.get(url,{'q':'Москва'})
        self.assertEqual(response.status_code,200)
        content=b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(io.StringIO(content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),2)
        self.assertIn('Москва',rows[1]); self.assertIn('reader@example.com',rows[1])
        self.assertIn('\'=HYPERLINK("bad")',rows[1])
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.client.post(url).status_code,405)

    def test_csv_permission_and_dashboard_period_are_enforced(self):
        url=reverse('admin:library_pagevisit_csv')
        self.assertEqual(self.client.get(url).status_code,302)
        self.staff(permit=False)
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.get(reverse('admin:library_pagevisit_dashboard'))

    def test_local_geoip_is_saved_and_nonpublic_ips_are_not_looked_up(self):
        from .geolocation import lookup, _lookup_cached
        _lookup_cached.cache_clear()
        fake={'city':{'names':{'ru':'Москва','en':'Moscow'}},'country':{'names':{'en':'Russia'}},
              'subdivisions':[{'names':{'ru':'Москва'}}]}
        with patch('library.geolocation.database_signature',return_value=('fixture.mmdb',1,1)), \
             patch('library.geolocation.maxminddb.open_database') as opening:
            opening.return_value.get.return_value=fake
            result=lookup('8.8.8.8')
            self.assertEqual(result['city'],'Москва')
            self.assertEqual(lookup('127.0.0.1')['city'],'')
            self.assertEqual(lookup('10.0.0.1')['city'],'')
            self.assertEqual(lookup('invalid')['city'],'')
            self.assertEqual(opening.call_count,1)
        with patch('library.analytics.lookup',return_value=result):
            self.client.get(reverse('home'),REMOTE_ADDR='8.8.8.8')
        row=PageVisit.objects.get()
        self.assertEqual((row.ip_address,row.city,row.region),('8.8.8.8','Москва','Москва'))

    def test_csv_includes_other_pages_and_respects_bot_filter(self):
        self.staff()
        PageVisit.objects.bulk_create([PageVisit(path='/csv-many/',visitor_key=str(n),is_bot=n>=120) for n in range(123)])
        response=self.client.get(reverse('admin:library_pagevisit_csv'),{'q':'csv-many','is_bot__exact':'0'})
        rows=list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),121)
        self.assertTrue(all(row[16]=='Нет' for row in rows[1:]))

    def test_missing_geoip_does_not_lose_visits_and_existing_rows_can_be_enriched(self):
        from .geolocation import enrich_visits
        self.client.get(reverse('home'),REMOTE_ADDR='8.8.8.8')
        row=PageVisit.objects.get()
        self.assertEqual(row.city,''); self.assertFalse(row.geo_checked)
        with patch('library.geolocation.available',return_value=True), \
             patch('library.geolocation.lookup',return_value={'city':'Москва','region':'','country':'Россия',
                 'geo_source':'DB-IP City Lite','geo_checked':True}):
            self.assertEqual(enrich_visits(),1)
        row.refresh_from_db(); self.assertEqual(row.city,'Москва')
