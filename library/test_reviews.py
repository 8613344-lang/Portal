import io
import tempfile
import uuid
from datetime import timedelta

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .images import image_url
from .models import Book, CustomerReview, Order, ReviewInvitation


def review_photo(name='family.jpg'):
    output = io.BytesIO()
    Image.new('RGB', (720, 960), '#d6c398').save(output, 'JPEG', quality=88)
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/jpeg')


def review_video(name='reaction.mp4'):
    return SimpleUploadedFile(name, b'\x00\x00\x00\x18ftypisom' + b'\x00' * 240,
                              content_type='video/mp4')


@override_settings(
    SITE_URL='https://qicko.ru', RATE_LIMIT_ENABLED=False, STATIC_ROOT=None,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    STORAGES={
        'default': {'BACKEND': 'library.storage.PrivateStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class CustomerReviewTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        media = override_settings(MEDIA_ROOT=directory.name)
        media.enable()
        self.addCleanup(media.disable)
        self.book = Book.objects.create(
            title='Сказка для Лизы', slug='review-liza', status='published', visibility='public')
        self.invitation = ReviewInvitation.objects.create(customer_name='Анна', book=self.book)
        self.url = reverse('review-submit', args=[self.invitation.token])

    def submit(self, **overrides):
        data = {
            'author_name': 'Анна', 'rating': '5',
            'text': 'Лиза узнала себя на первой странице и просит читать книгу каждый вечер.',
            'publication_consent': 'on',
        }
        data.update({key: value for key, value in overrides.items() if key not in ('photo', 'video')})
        data.update({key: overrides[key] for key in ('photo', 'video') if key in overrides})
        return self.client.post(self.url, data)

    def test_personal_link_is_private_prefilled_and_not_search_indexed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Расскажите о вашей книге')
        self.assertContains(response, 'value="Анна"')
        self.assertContains(response, self.book.title)
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        public = self.client.get(reverse('reviews'))
        self.assertNotContains(public, str(self.invitation.token))
        self.assertEqual(self.client.get(reverse('review-submit', args=[uuid.uuid4()])).status_code, 404)

    def test_expired_disabled_and_used_links_cannot_accept_reviews(self):
        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save(update_fields=['expires_at'])
        self.assertEqual(self.client.get(self.url).status_code, 410)
        self.invitation.expires_at = timezone.now() + timedelta(days=1)
        self.invitation.active = False
        self.invitation.save(update_fields=['expires_at', 'active'])
        self.assertEqual(self.client.post(self.url, {}).status_code, 410)

    def test_review_with_photo_and_video_stays_private_until_moderation(self):
        response = self.submit(photo=review_photo(), video=review_video())
        self.assertRedirects(response, reverse('review-thanks'))
        review = CustomerReview.objects.get()
        self.invitation.refresh_from_db()
        self.assertEqual(review.status, CustomerReview.Status.PENDING)
        self.assertTrue(self.invitation.used_at)
        self.assertFalse(self.invitation.active)
        self.assertEqual(self.client.get(self.url).status_code, 410)
        self.assertNotContains(self.client.get(reverse('reviews')), review.author_name)
        self.assertNotContains(self.client.get(reverse('home')), review.text)
        self.assertEqual(self.client.get(image_url(review.photo, 640)).status_code, 404)
        self.assertEqual(self.client.get(review.photo.url).status_code, 404)
        self.assertEqual(self.client.get(reverse('review-video', args=[review.pk])).status_code, 404)

        review.status = CustomerReview.Status.PUBLISHED
        review.featured = True
        review.save(update_fields=['status', 'featured', 'published_at'])
        listing = self.client.get(reverse('reviews'))
        self.assertContains(listing, review.author_name)
        self.assertContains(listing, review.text)
        self.assertContains(listing, self.book.title)
        self.assertNotContains(listing, str(self.invitation.token))
        self.assertContains(self.client.get(reverse('home')), review.text)
        photo = self.client.get(image_url(review.photo, 640))
        self.assertEqual((photo.status_code, photo['Content-Type']), (200, 'image/webp'))
        photo.close()
        clip = self.client.get(reverse('review-video', args=[review.pk]), HTTP_RANGE='bytes=0-11')
        self.assertEqual((clip.status_code, clip['Content-Type'], clip['Content-Length']), (206, 'video/mp4', '12'))
        clip.close()

    def test_invalid_or_unapproved_submission_does_not_consume_link(self):
        response = self.submit(publication_consent='')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Обязательное поле')
        self.assertFalse(CustomerReview.objects.exists())
        self.invitation.refresh_from_db()
        self.assertTrue(self.invitation.available)

        response = self.submit(photo=SimpleUploadedFile('fake.jpg', b'<script>bad</script>'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomerReview.objects.exists())
        self.invitation.refresh_from_db()
        self.assertTrue(self.invitation.available)

    def test_public_review_escapes_customer_content(self):
        response = self.submit(author_name='<img src=x onerror=badName()>',
                               text='<script>badReview()</script> Очень понравилось')
        self.assertEqual(response.status_code, 302)
        review = CustomerReview.objects.get()
        review.status = CustomerReview.Status.PUBLISHED
        review.save(update_fields=['status', 'published_at'])
        listing = self.client.get(reverse('reviews'))
        self.assertNotContains(listing, '<script>badReview()</script>', html=True)
        self.assertNotContains(listing, '<img src=x onerror=badName()>', html=True)
        self.assertContains(listing, '&lt;script&gt;badReview()&lt;/script&gt;', html=False)

    def test_public_review_does_not_reveal_restricted_book_title(self):
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        review = CustomerReview.objects.get()
        review.status = CustomerReview.Status.PUBLISHED
        review.save(update_fields=['status', 'published_at'])
        self.book.visibility = 'restricted'
        self.book.save(update_fields=['visibility'])

        listing = self.client.get(reverse('reviews'))
        self.assertContains(listing, review.text)
        self.assertNotContains(listing, self.book.title)

    def test_admin_order_action_generates_link_and_moderates_review(self):
        order = Order.objects.create(
            submission_key=uuid.uuid4(), parent_name='Мария', email='maria@example.test',
            child_name='Миша', child_age=5, event='День рождения', book=self.book)
        staff = User.objects.create_superuser('review-admin', 'admin@example.test', 'test')
        self.client.force_login(staff)
        response = self.client.post(reverse('admin:library_order_changelist'), {
            'action': 'create_review_invitations', '_selected_action': [str(order.pk)], 'index': '0'})
        self.assertEqual(response.status_code, 302)
        generated = ReviewInvitation.objects.exclude(pk=self.invitation.pk).get(order=order)
        self.assertEqual((generated.customer_name, generated.book_id), ('Мария', self.book.pk))
        change = self.client.get(reverse('admin:library_reviewinvitation_change', args=[generated.pk]))
        self.assertContains(change, generated.share_url)

        review = CustomerReview.objects.create(
            invitation=generated, author_name='Мария', rating=5, text='Спасибо!', publication_consent=True)
        response = self.client.post(reverse('admin:library_customerreview_changelist'), {
            'action': 'publish_reviews', '_selected_action': [str(review.pk)], 'index': '0'})
        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.status, CustomerReview.Status.PUBLISHED)
        self.assertTrue(review.published_at)
