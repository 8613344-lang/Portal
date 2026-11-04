from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .commerce import OCCASIONS
from .models import BookTariff, LegalDocument, Order, OrderFunnelEvent


@override_settings(RATE_LIMIT_ENABLED=False, ANALYTICS_ENABLED=True, STATIC_ROOT=None,
    STORAGES={'default': {'BACKEND': 'library.storage.PrivateStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OrderFunnelTests(TestCase):
    def start(self, **query):
        response = self.client.get(reverse('order'), query)
        return response, {'submission_key': response.context['submission_key']}

    def brief(self, **changes):
        return {'child_name': 'Лена', 'child_age': 5, 'occasion': 'День рождения', **changes}

    def test_guest_brief_then_submit_records_stages_without_form_contents(self):
        tariff = BookTariff.objects.get(price=500)
        start, key = self.start(tariff=tariff.pk)
        self.assertNotContains(start, 'name="email"')
        self.assertEqual(len(OCCASIONS), 15)
        for occasion in OCCASIONS:
            self.assertContains(start, occasion)
        self.assertEqual(self.client.post(reverse('order-start'), key).status_code, 204)
        invalid = self.client.post(reverse('order'), key | self.brief(occasion='Другое') | {'stage': 'brief'})
        self.assertContains(invalid, 'Напишите пару слов о вашем поводе.')
        step = self.client.post(reverse('order'), key | self.brief() | {'stage': 'brief', 'preferred_tariff': tariff.pk})
        self.assertContains(step, 'Шаг 2 из 2')
        self.assertEqual(step.context['form']['tariff'].value(), str(tariff.pk))
        self.assertEqual(Order.objects.count(), 0)
        data = key | self.brief() | {'parent_name': 'Гость', 'email': 'guest@example.test', 'consent': 'on',
                                     'tariff': tariff.pk, 'narration': 'on', 'printed': 'on', 'stage': 'submit'}
        response = self.client.post(reverse('order'), data | {'email': 'invalid'})
        self.assertContains(response, 'Проверьте выделенные поля')
        for _ in range(2):
            self.assertRedirects(self.client.post(reverse('order'), data), reverse('order-success'))
        order = Order.objects.get()
        self.assertIsNone(order.user)
        self.assertEqual(order.event, 'День рождения')
        self.assertEqual(order.child_description, '')
        self.assertEqual(order.pricing['итого без доставки и твёрдого переплёта'], 740)
        self.assertCountEqual(OrderFunnelEvent.objects.values_list('kind', 'error_fields'), [
            ('start', ''), ('details', ''), ('error', 'event'), ('error', 'email'), ('submitted', ''),
        ])

    def test_admin_tariff_changes_reach_all_public_entry_points(self):
        tariff = BookTariff.objects.get(price=500)
        tariff.price = 537
        tariff.save(update_fields=['price'])
        for route in ('home', 'book-service', 'order'):
            response = self.client.get(reverse(route))
            self.assertContains(response, '537 ₽')
            self.assertContains(response, '627 ₽')
            self.assertContains(response, 'Доставка оплачивается отдельно.')

    def test_consent_version_checked_after_document_changes(self):
        document = LegalDocument.objects.get(kind='consent')
        document.content = 'Редакция 1'
        document.save()
        previous = document.fingerprint
        _, key = self.start()
        document.content = 'Редакция 2 <script>test</script>'
        document.save()
        legal = self.client.get(document.get_absolute_url())
        self.assertContains(legal, 'Редакция 2 &lt;script&gt;test&lt;/script&gt;')
        data = key | self.brief() | {'parent_name': 'Имя', 'email': 'guest@example.test', 'consent': 'on'}
        invalid = self.client.post(reverse('order'), data | {'consent_version': previous})
        self.assertContains(invalid, 'Согласие обновилось')
        self.assertFalse(invalid.context['form']['consent'].value())
        self.assertFalse(Order.objects.exists())
        self.assertRedirects(self.client.post(reverse('order'), data | {'consent_version': document.fingerprint}), reverse('order-success'))
        self.assertEqual(Order.objects.get().consent_version, document.fingerprint)

    def test_staff_marks_qualification_and_payment_independently_of_order_status(self):
        _, key = self.start()
        self.client.post(reverse('order'), key | self.brief() | {
            'parent_name': 'Гость', 'email': 'guest@example.test', 'consent': 'on',
        })
        order = Order.objects.get()
        report = reverse('admin:library_orderfunnelevent_changelist')
        self.assertRedirects(self.client.get(report), f"{reverse('admin:login')}?next={report}", fetch_redirect_response=False)
        self.client.force_login(User.objects.create_superuser('staff', 'staff@example.test', 'password'))
        url = reverse('admin:library_order_changelist')
        for action in ('confirm_payment', 'qualify_orders', 'qualify_orders', 'confirm_payment', 'confirm_payment'):
            self.assertEqual(self.client.post(url, {'action': action, '_selected_action': str(order.pk)}).status_code, 302)
            if action == 'qualify_orders':
                order.refresh_from_db()
                self.assertIsNone(order.paid_at)
        order.refresh_from_db()
        self.assertEqual(order.status, 'new')
        self.assertLessEqual(order.qualified_at, order.paid_at)
        self.assertEqual(OrderFunnelEvent.objects.filter(kind='qualified').count(), 1)
        self.assertEqual(OrderFunnelEvent.objects.filter(kind='paid').count(), 1)
        self.assertContains(self.client.get(report), 'Уникальных форм')

    def test_install_nudge_is_at_end_of_sample_only(self):
        for route in ('home', 'order'):
            self.assertNotContains(self.client.get(reverse(route)), 'data-install-offer')
        sample = self.client.get(reverse('sample-book'))
        self.assertContains(sample, 'Мира и лисёнок')
        self.assertContains(sample, 'data-reading-end')
        self.assertContains(sample, 'data-install-offer hidden')
