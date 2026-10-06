from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from .models import RateEvent

class RateLimitMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        limits = {'/order/': 10, '/accounts/login/': 20, '/admin/login/': 20, '/accounts/register/': 10}
        if settings.RATE_LIMIT_ENABLED and request.method == 'POST' and request.path in limits:
            # Nginx overwrites this header. Production app is not exposed directly.
            ip = request.META.get('HTTP_X_REAL_IP') if settings.BEHIND_PROXY else request.META.get('REMOTE_ADDR')
            key = salted_hmac('rate-limit', f'{ip}:{request.path}').hexdigest()
            cutoff = timezone.now() - timedelta(minutes=15)
            with transaction.atomic():
                if RateEvent.objects.filter(key=key, created_at__gte=cutoff).count() >= limits[request.path]:
                    response = HttpResponse('Слишком много попыток. Попробуйте через 15 минут.', status=429)
                    response['Retry-After'] = '900'
                    return response
                RateEvent.objects.create(key=key)
        return self.get_response(request)
