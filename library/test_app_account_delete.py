import uuid
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Book, BookAccess, EmailConfirmation, Notification, NotificationRecipient, Order


@override_settings(
    RATE_LIMIT_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STATIC_ROOT=None,
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class AppAccountDeleteTests(TestCase):
    def setUp(self):
        self.password = 'reader-password'
        self.user = User.objects.create_user('delete-reader', email='reader@example.com', password=self.password)
        self.other = User.objects.create_user('keep-reader', email='other@example.com', password='other-password')
        self.book = Book.objects.create(title='Личная книга', slug='delete-personal', status='published')
        BookAccess.objects.create(user=self.user, book=self.book)
        BookAccess.objects.create(user=self.other, book=self.book)
        self.url = reverse('app-account-delete') + '?device=tablet'

    def valid_post(self):
        return {'password': self.password, 'confirm': 'on'}

    def test_login_required_get_is_read_only_and_keeps_device(self):
        for method in (self.client.get, self.client.post):
            response = method(self.url, self.valid_post())
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith('/app/login/?next='))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'app_base.html')
        self.assertEqual(response.context['device'], 'tablet')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'Сами книги, заявки и сведения, указанные в заявках, сохраняются отдельно.')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_wrong_password_or_missing_confirmation_cannot_delete(self):
        self.client.force_login(self.user)
        for data in ({'password': 'incorrect', 'confirm': 'on'}, {'password': self.password}, {}):
            with self.subTest(data=list(data)):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
                self.assertTrue(BookAccess.objects.filter(user=self.user, book=self.book).exists())
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))

    def test_csrf_required_even_with_correct_password(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, self.valid_post()).status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        client.get(self.url)
        token = client.cookies['csrftoken'].value
        response = client.post(self.url, {**self.valid_post(), 'csrfmiddlewaretoken': token})
        self.assertRedirects(response, '/app/?device=tablet&account_deleted=1', fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())

    def test_deletion_is_scoped_to_current_user_and_preserves_books_orders_and_notifications(self):
        confirmation = EmailConfirmation.objects.create(user=self.user)
        order = Order.objects.create(
            submission_key=uuid.uuid4(), user=self.user, manager=self.other,
            parent_name='Родитель', email=self.user.email, child_name='Ребёнок',
            child_age=5, child_description='Описание', event='Событие', book=self.book,
        )
        recipient = NotificationRecipient.objects.create(
            name='Бывший сотрудник', channel='email', destination='recipient@example.com', employee=self.user,
        )
        notification = Notification.objects.create(
            order=order, recipient=recipient, destination=recipient.destination, channel='email',
        )
        old_session = Client()
        old_session.force_login(self.user)
        other_session = Client()
        other_session.force_login(self.other)
        self.client.force_login(self.user)
        response = self.client.post(self.url, {**self.valid_post(), 'user_id': self.other.pk, 'next': 'https://outside.invalid/'})
        self.assertRedirects(response, '/app/?device=tablet&account_deleted=1', fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.other.pk).exists())
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(EmailConfirmation.objects.filter(pk=confirmation.pk).exists())
        self.assertEqual(list(BookAccess.objects.values_list('user_id', flat=True)), [self.other.pk])
        order.refresh_from_db()
        self.assertIsNone(order.user_id)
        self.assertEqual(order.manager_id, self.other.pk)
        self.assertEqual(order.email, 'reader@example.com')
        self.assertEqual(order.book_id, self.book.pk)
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())
        recipient.refresh_from_db()
        self.assertIsNone(recipient.employee_id)
        self.assertFalse(recipient.active)
        self.assertFalse(old_session.get(reverse('app-shelf')).context['user'].is_authenticated)
        self.assertEqual(old_session.get(reverse('reader', args=[self.book.slug]), {'app': '1'}).status_code, 404)
        self.assertTrue(other_session.get(reverse('app-shelf')).context['user'].is_authenticated)
        self.assertEqual(other_session.get(reverse('reader', args=[self.book.slug]), {'app': '1'}).status_code, 200)

    def test_failed_deletion_rolls_back_related_record_changes(self):
        recipient = NotificationRecipient.objects.create(
            name='Бывший сотрудник', channel='email', destination='rollback@example.com', employee=self.user,
        )
        self.client.force_login(self.user)
        with patch.object(User, 'delete', side_effect=RuntimeError('Simulated database failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.valid_post())
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        recipient.refresh_from_db()
        self.assertEqual(recipient.employee_id, self.user.pk)
        self.assertTrue(recipient.active)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))

    def test_employee_and_superuser_deletion_is_blocked(self):
        for flags in ({'is_staff': True}, {'is_superuser': True}):
            with self.subTest(flags=flags):
                user = User.objects.create_user('protected-' + str(len(flags)) + str(User.objects.count()), password=self.password, **flags)
                self.client.force_login(user)
                for method in (self.client.get, self.client.post):
                    response = method(self.url, self.valid_post())
                    self.assertEqual(response.status_code, 403)
                    self.assertContains(response, 'нельзя удалить из приложения', status_code=403)
                    self.assertNotContains(response, 'Удалить мой аккаунт навсегда', status_code=403)
                    self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_privacy_and_delete_are_accessible_inside_app_without_website_navigation(self):
        anonymous = self.client.get(reverse('app-shelf'), {'device': 'tablet'})
        self.assertContains(anonymous, '/privacy/?app=1&amp;device=tablet')
        self.assertNotContains(anonymous, '/app/account/delete/')
        self.assertContains(anonymous, '<meta name="portal-account" content="guest">', html=True)
        self.client.force_login(self.user)
        shelf = self.client.get(reverse('app-shelf'), {'device': 'tablet'})
        self.assertContains(shelf, '/app/account/delete/?device=tablet')
        self.assertContains(shelf, f'<meta name="portal-account" content="{self.user.pk}">', html=True)
        self.assertContains(shelf, '<meta name="portal-account-label" content="reader@example.com">', html=True)
        privacy = self.client.get(reverse('privacy'), {'app': '1', 'device': 'tablet'})
        self.assertTemplateUsed(privacy, 'app_base.html')
        self.assertNotContains(privacy, 'Создать свою книгу')
        self.assertContains(privacy, '/app/?device=tablet')
        website = self.client.get(reverse('privacy'))
        self.assertTemplateUsed(website, 'base.html')
        self.assertFalse(website.context['app_mode'])

    @override_settings(RATE_LIMIT_ENABLED=True)
    def test_repeated_password_attempts_are_limited(self):
        self.client.force_login(self.user)
        for attempt in range(5):
            self.assertEqual(self.client.post(self.url, {'password': 'wrong', 'confirm': 'on'}).status_code, 200)
        response = self.client.post(self.url, self.valid_post())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '900')
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
