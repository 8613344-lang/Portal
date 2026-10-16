from django.contrib.auth.models import Permission, User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import UserAgreement


@override_settings(
    RATE_LIMIT_ENABLED=False,
    STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class UserAgreementTests(TestCase):
    def setUp(self):
        self.agreement = UserAgreement.objects.get(pk=1)
        self.change_url = reverse('admin:library_useragreement_change', args=[1])
        self.add_url = reverse('admin:library_useragreement_add')
        self.delete_url = reverse('admin:library_useragreement_delete', args=[1])

    def staff_with_permissions(self, *codenames):
        user = User.objects.create_user(
            username='agreement-editor', email='editor@example.com', is_staff=True,
        )
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='library',
            content_type__model='useragreement',
            codename__in=codenames,
        ))
        self.client.force_login(user)
        return user

    def test_seeded_agreement_is_public_without_sign_in(self):
        response = self.client.get(reverse('privacy'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['agreement'].pk, 1)
        self.assertContains(response, 'Пользовательское соглашение')
        self.assertContains(response, 'Фотография не обязательна для оформления заявки.')

    def test_authorized_staff_can_edit_and_public_page_updates_immediately(self):
        self.staff_with_permissions('change_useragreement')
        self.assertEqual(self.client.get(self.change_url).status_code, 200)
        response = self.client.post(self.change_url, {
            'title': 'Соглашение с читателями',
            'content': 'Первый абзац.\n\nВторой абзац.',
            '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302)
        self.agreement.refresh_from_db()
        self.assertEqual(self.agreement.title, 'Соглашение с читателями')
        self.assertEqual(self.agreement.content, 'Первый абзац.\n\nВторой абзац.')
        self.assertIsNotNone(self.agreement.updated_at)
        self.client.logout()
        response = self.client.get(reverse('privacy'))
        self.assertContains(response, 'Соглашение с читателями')
        self.assertContains(response, '<p>Первый абзац.</p>', html=True)
        self.assertContains(response, '<p>Второй абзац.</p>', html=True)

    def test_anonymous_and_nonstaff_cannot_edit_in_admin(self):
        for user in (None, User.objects.create_user('ordinary-reader')):
            if user is not None:
                self.client.force_login(user)
            response = self.client.post(self.change_url, {
                'title': 'Постороннее изменение', 'content': 'Изменено без разрешения',
            })
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('admin:login'), response.url)
        self.agreement.refresh_from_db()
        self.assertNotEqual(self.agreement.title, 'Постороннее изменение')

    def test_staff_without_model_permission_cannot_view_or_edit(self):
        self.staff_with_permissions()
        self.assertEqual(self.client.get(self.change_url).status_code, 403)
        self.assertEqual(self.client.post(self.change_url, {
            'title': 'Без разрешения', 'content': 'Чужое изменение',
        }).status_code, 403)
        self.agreement.refresh_from_db()
        self.assertNotEqual(self.agreement.title, 'Без разрешения')

    def test_view_permission_does_not_allow_editing(self):
        self.staff_with_permissions('view_useragreement')
        self.assertEqual(self.client.get(self.change_url).status_code, 200)
        self.assertEqual(self.client.post(self.change_url, {
            'title': 'Только просмотр', 'content': 'Попытка изменения',
        }).status_code, 403)
        self.agreement.refresh_from_db()
        self.assertNotEqual(self.agreement.title, 'Только просмотр')

    def test_admin_cannot_add_second_agreement_or_delete_existing_one(self):
        self.client.force_login(User.objects.create_superuser(
            'agreement-admin', 'admin@example.com', 'test-password',
        ))
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.add_url).status_code, 403)
            self.assertEqual(method(self.delete_url).status_code, 403)
        self.assertEqual(UserAgreement.objects.count(), 1)

    def test_database_rejects_second_singleton_row(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserAgreement.objects.create(pk=2, title='Второе соглашение', content='Текст')
        self.assertEqual(UserAgreement.objects.count(), 1)

    def test_agreement_content_is_escaped_and_preserves_paragraphs(self):
        self.agreement.title = '<script>badTitle()</script>'
        self.agreement.content = '<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>'
        self.agreement.save()
        response = self.client.get(reverse('privacy'))
        self.assertContains(response, '&lt;script&gt;badTitle()&lt;/script&gt;')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertContains(response, '&lt;img src=x onerror=alert(2)&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '<img src=x onerror=alert(2)>')

    def test_missing_agreement_uses_defaults_without_writing_on_public_get(self):
        UserAgreement.objects.all().delete()
        response = self.client.get(reverse('privacy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Пользовательское соглашение')
        self.assertContains(response, 'Фотография не обязательна для оформления заявки.')
        self.assertEqual(UserAgreement.objects.count(), 0)
