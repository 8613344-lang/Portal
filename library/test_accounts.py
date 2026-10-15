import re
from datetime import timedelta
from django.core import mail
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from .models import EmailConfirmation


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', RATE_LIMIT_ENABLED=False, STATIC_ROOT=None, STORAGES={'default':{'BACKEND':'library.storage.PrivateStorage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class AccountTests(TestCase):
    def test_long_valid_email_login_and_legacy_staff_admin_login(self):
        address = 'x'*60+'@'+'a'*60+'.'+'b'*60+'.example.com'
        user = User.objects.create_user('long-address',email=address,password='GoodPassword-book-836')
        self.assertEqual(self.client.post(reverse('login'),{'username':address,'password':'GoodPassword-book-836'}).status_code,302)
        self.client.logout()
        staff = User.objects.create_superuser('legacy-admin','', 'GoodPassword-book-836')
        self.assertEqual(self.client.post(reverse('admin:login'),{'username':staff.username,'password':'GoodPassword-book-836','next':'/admin/'}).status_code,302)

    def register(self, address='test@example.com', name='Анна'):
        session = self.client.session
        session['captcha'] = [salted_hmac('captcha','ABC234').hexdigest(), timezone.now().timestamp()]
        session.save()
        return self.client.post(reverse('register'),{'name':name,'email':address,'password1':'GoodPassword-book-836','password2':'GoodPassword-book-836','captcha':'abc234','consent':'on'})

    def test_captcha_required_one_use_and_confirmation_required(self):
        response = self.client.get(reverse('captcha'))
        self.assertEqual(response['Content-Type'],'image/png')
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.register().status_code,302)
        user = User.objects.get(email='test@example.com')
        self.assertFalse(user.is_active)
        self.assertFalse(self.client.login(username=user.email,password='GoodPassword-book-836'))
        self.assertNotIn('_auth_user_id',self.client.session)
        code = re.search(r'\b(\d{6})\b',mail.outbox[-1].body)[1]
        self.assertRedirects(self.client.post(reverse('email-confirm'),{'code':code}),reverse('profile'))
        user.refresh_from_db(); self.assertTrue(user.is_active)
        self.assertIsNotNone(EmailConfirmation.objects.get(user=user).verified_at)
        self.assertTrue(self.client.login(username='TEST@EXAMPLE.COM',password='GoodPassword-book-836'))

    def test_names_repeat_but_emails_unique_and_username_not_login(self):
        self.register('one@example.com')
        self.register('two@example.com')
        self.assertEqual(User.objects.filter(first_name='Анна').count(),2)
        self.assertEqual(self.register('ONE@example.com').status_code,200)
        self.assertEqual(User.objects.count(),2)
        user = User.objects.first()
        user.is_active = True; user.save()
        self.assertFalse(Client().login(username=user.username,password='GoodPassword-book-836'))
        with self.assertRaises(IntegrityError), transaction.atomic(): User.objects.create(username='duplicate',email=user.email.upper())

    def test_bad_captcha_cannot_register(self):
        self.client.post(reverse('register'),{'name':'Анна','email':'none@example.com','password1':'GoodPassword-book-836','password2':'GoodPassword-book-836','captcha':'ABC234','consent':'on'})
        self.assertEqual(User.objects.count(),0)

    def test_confirmation_can_resume_from_new_browser_session(self):
        self.register()
        contact = EmailConfirmation.objects.get()
        contact.sent_at = timezone.now()-timedelta(minutes=2); contact.save()
        other = Client()
        self.assertRedirects(other.post(reverse('email-start'),{'email':'TEST@example.com'}),reverse('email-confirm'))
        code = re.search(r'\b(\d{6})\b',mail.outbox[-1].body)[1]
        self.assertRedirects(other.post(reverse('email-confirm'),{'code':code}),reverse('profile'))

    def test_code_attempt_limit_expiry_and_resend_throttle(self):
        self.register()
        contact = EmailConfirmation.objects.get()
        code = re.search(r'\b(\d{6})\b',mail.outbox[-1].body)[1]
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5): self.client.post(reverse('email-confirm'),{'code':wrong})
        self.client.post(reverse('email-confirm'),{'code':code})
        contact.refresh_from_db(); self.assertEqual(contact.attempts,5)
        self.assertFalse(User.objects.get().is_active)
        self.client.post(reverse('email-resend')); self.assertEqual(len(mail.outbox),1)
        contact.sent_at = timezone.now()-timedelta(minutes=2); contact.save()
        self.client.post(reverse('email-resend')); self.assertEqual(len(mail.outbox),2)
        contact.refresh_from_db(); contact.expires_at = timezone.now()-timedelta(seconds=1); contact.save()
        code = re.search(r'\b(\d{6})\b',mail.outbox[-1].body)[1]
        self.client.post(reverse('email-confirm'),{'code':code})
        self.assertFalse(User.objects.get().is_active)

    def test_profile_and_password_recovery(self):
        user = User.objects.create_user('old-user',email='reader@example.com',password='GoodPassword-book-836',first_name='Анна')
        self.client.force_login(user)
        self.client.post(reverse('profile'),{'name':'Сергей'})
        user.refresh_from_db(); self.assertEqual(user.first_name,'Сергей')
        self.client.logout()
        self.assertEqual(self.client.post(reverse('password_reset'),{'email':user.email}).status_code,302)
        url = re.search(r'https?://[^\s]+',mail.outbox[-1].body)[0]
        response = self.client.get(url,follow=True)
        path = response.redirect_chain[-1][0]
        self.assertEqual(self.client.post(path,{'new_password1':'AnotherGoodPassword-987','new_password2':'AnotherGoodPassword-987'}).status_code,302)
        user.refresh_from_db(); self.assertTrue(user.check_password('AnotherGoodPassword-987'))
        self.assertFalse(self.client.login(username=user.email,password='GoodPassword-book-836'))
        self.assertTrue(self.client.login(username=user.email,password='AnotherGoodPassword-987'))
