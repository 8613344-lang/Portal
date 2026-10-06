import json
import uuid
from datetime import timedelta
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from .models import BotState, Notification, NotificationRecipient, Order

def telegram_call(method, payload):
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError('Telegram is not configured')
    request = Request(f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    # Exceptions may contain the URL/token: caller stores only the exception type.
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not result.get('ok'): raise RuntimeError('Telegram rejected request')
    return result['result']

def claim_notification():
    now = timezone.now()
    with transaction.atomic():
        row = Notification.objects.select_for_update().filter(Q(state='pending') | Q(state='sending'), next_attempt__lte=now).order_by('next_attempt', 'pk').first()
        if not row: return None
        row.state = 'sending'
        row.lease_token = uuid.uuid4()
        row.attempts += 1
        row.next_attempt = now + timedelta(minutes=5)
        row.save(update_fields=['state', 'lease_token', 'attempts', 'next_attempt'])
        return row

def deliver_one():
    row = claim_notification()
    if row is None: return False
    # Recheck permission immediately before sending, including revoked employees.
    recipient = NotificationRecipient.objects.select_related('employee').get(pk=row.recipient_id)
    if not recipient.allowed() or recipient.destination != row.destination or recipient.channel != row.channel:
        Notification.objects.filter(pk=row.pk, lease_token=row.lease_token).update(state='cancelled', last_error='Recipient disabled or changed')
        return True
    url = settings.SITE_URL + reverse('admin:library_order_change', args=[row.order_id])
    body = f'Новый заказ персональной книги: {row.order_id}\nОткрыть защищённую карточку: {url}\nВойдите с учётной записью сотрудника.'
    try:
        if row.channel == 'email':
            send_mail('Новый заказ · Книги рядом', body, settings.DEFAULT_FROM_EMAIL, [row.destination], fail_silently=False)
        else:
            telegram_call('sendMessage', {'chat_id': int(row.destination), 'text': body, 'link_preview_options': {'is_disabled': True}})
    except Exception as exc:
        state = 'failed' if row.attempts >= settings.NOTIFICATION_MAX_ATTEMPTS else 'pending'
        delay = min(3600, 30 * 2 ** min(row.attempts, 7))
        Notification.objects.filter(pk=row.pk, lease_token=row.lease_token).update(state=state, last_error=type(exc).__name__, next_attempt=timezone.now() + timedelta(seconds=delay), lease_token=None)
    else:
        Notification.objects.filter(pk=row.pk, lease_token=row.lease_token).update(state='sent', sent_at=timezone.now(), last_error='', lease_token=None)
    return True

def handle_bot_update(update):
    message = update.get('message', {})
    sender = message.get('from', {})
    chat = message.get('chat', {})
    if sender.get('is_bot') or chat.get('type') != 'private' or not sender.get('id'):
        return
    telegram_id = str(sender['id'])
    recipient = NotificationRecipient.objects.select_related('employee').filter(channel='telegram', destination=telegram_id, active=True).first()
    if not recipient or not recipient.allowed():
        text = f'Доступ к заказам не выдан. Ваш Telegram ID: {telegram_id}. Передайте его администратору платформы.'
    elif message.get('text', '').split('@')[0] in ['/start', '/help']:
        text = 'Уведомления подключены. /orders — последние заказы. Полные карточки и фотографии доступны после входа в админку.'
    elif message.get('text', '').split('@')[0] == '/orders':
        rows = list(Order.objects.order_by('-created_at')[:5])
        text = '\n\n'.join(f'{order.id}\n{settings.SITE_URL}{reverse("admin:library_order_change", args=[order.id])}' for order in rows) or 'Заказов пока нет.'
    else:
        text = 'Используйте /orders или /help.'
    telegram_call('sendMessage', {'chat_id': sender['id'], 'text': text, 'link_preview_options': {'is_disabled': True}})

def poll_bot():
    if not settings.TELEGRAM_BOT_TOKEN: return
    state, _ = BotState.objects.get_or_create(name='telegram')
    updates = telegram_call('getUpdates', {'offset': state.offset, 'timeout': 0, 'limit': 20, 'allowed_updates': ['message']})
    for update in updates:
        handle_bot_update(update)
        state.offset = update['update_id'] + 1
        state.save(update_fields=['offset'])
