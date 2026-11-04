from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from .models import BookTariff, OrderFunnelEvent


OCCASIONS = (
    'День рождения', 'Первый день в детском саду', 'Первый день в школе',
    'Выпускной', 'Рождение брата или сестры', 'Семейная поездка',
    'Переезд', 'Новый год', 'Каникулы у бабушки и дедушки',
    'Новая дружба', 'Первый питомец', 'Победа или достижение',
    'Маленький подвиг', 'Любимое увлечение', 'Подарок без повода',
)
OTHER_OCCASION = 'Другое'
RESPONSE_TIME = 'Ответим по email в течение дня.'
PRODUCTION_TIME = 'Изготовление — до 2 дней после согласования задания.'
REVIEW_TERMS = 'Покажем результат, согласуем его и внесём правки без ограничения их количества. Оплата — только после результата.'
DELIVERY_TERMS = 'Доставка оплачивается отдельно. Стоимость зависит от вашего города — подберём оптимальный вариант.'
HARDCOVER_TERMS = 'Твёрдый переплёт обсуждаем отдельно: цена зависит от числа страниц и оформления обложки.'


def commerce_context(tariffs=None):
    return {
        'tariffs': list(BookTariff.objects.filter(enabled=True)) if tariffs is None else tariffs,
        'response_time': RESPONSE_TIME, 'production_time': PRODUCTION_TIME,
        'review_terms': REVIEW_TERMS, 'delivery_terms': DELIVERY_TERMS,
        'hardcover_terms': HARDCOVER_TERMS,
    }


def record_funnel(key, kind, error_fields=()):
    if not settings.ANALYTICS_ENABLED:
        return
    try:
        with transaction.atomic():
            if kind == OrderFunnelEvent.Kind.ERROR:
                OrderFunnelEvent.objects.create(submission_key=key, kind=kind, error_fields=','.join(sorted(error_fields))[:250])
            else:
                OrderFunnelEvent.objects.get_or_create(submission_key=key, kind=kind)
    except DatabaseError:
        # Reporting must not prevent receiving a valid order.
        import logging
        logging.getLogger(__name__).warning('Could not record order funnel event: %s', kind)


@transaction.atomic
def mark_orders(queryset, *, paid=False):
    now = timezone.now()
    pending = queryset.filter(paid_at__isnull=True, qualified_at__isnull=False) if paid else queryset.filter(qualified_at__isnull=True)
    keys = list(pending.values_list('submission_key', flat=True))
    changed = pending.update(**{'paid_at' if paid else 'qualified_at': now})
    if settings.ANALYTICS_ENABLED:
        OrderFunnelEvent.objects.bulk_create([
            OrderFunnelEvent(submission_key=key, kind=OrderFunnelEvent.Kind.PAID if paid else OrderFunnelEvent.Kind.QUALIFIED)
            for key in keys
        ], ignore_conflicts=True)
    return changed


def prune_funnel():
    cutoff = timezone.now() - timedelta(days=settings.ANALYTICS_RETENTION_DAYS)
    ids = list(OrderFunnelEvent.objects.filter(occurred_at__lt=cutoff).values_list('pk', flat=True)[:5000])
    if ids:
        OrderFunnelEvent.objects.filter(pk__in=ids).delete()
    return len(ids)
