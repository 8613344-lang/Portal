import json
import math
import re
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import Book, VideoPosition
from .videos import current_video, queue_video


def can_watch_video(request, book):
    from .views import can_read_book
    token = request.session.get('shared_videos', {}).get(str(book.pk))
    return token == str(book.video_share_token) or can_read_book(request, book)


def can_create_video(request, book):
    if book.visibility == 'public':
        return request.user.is_staff and request.user.has_perm('library.change_book')
    from .views import can_read_book
    return can_read_book(request, book)


@never_cache
@require_GET
def shared_video(request, token):
    book = get_object_or_404(Book, video_share_token=token)
    grants = request.session.get('shared_videos', {})
    grants.pop(str(book.pk), None)
    grants[str(book.pk)] = str(book.video_share_token)
    request.session['shared_videos'] = dict(list(grants.items())[-100:])
    response = watch(request, book.slug)
    response['Referrer-Policy'] = 'same-origin'
    return response


def allowed_book(request, slug):
    book = get_object_or_404(Book.objects.select_related('cover_page', 'ending_page'), slug=slug)
    if not can_watch_video(request, book): raise Http404
    return book


def payload(book):
    job, ready = current_video(book)
    state = 'missing' if not job else 'ready' if ready else 'stale' if job.state == 'ready' else job.state
    return {'state': state, 'revision': job.updated_at.isoformat() if ready else '', 'progress': job.progress if job else 0, 'total': job.total if job else 0,
            'delay': job.delay if job else 15, 'error': job.error if job and state == 'failed' else '',
            'file': reverse('book-video-file', args=[book.slug]) if ready else ''}


@never_cache
@require_GET
def watch(request, slug):
    from .views import can_read_book
    book = allowed_book(request, slug)
    request.visit_book, request.visit_kind = book, 'video'
    app = request.GET.get('app') == '1'
    response = render(request, 'library/video.html', {'book': book, 'video': payload(book), 'app_mode': app,
        'can_read_source': can_read_book(request, book),
        'can_manage_video': can_create_video(request, book),
        'device': 'tablet' if request.GET.get('device') == 'tablet' else 'phone',
        'base_template': 'app_base.html' if app else 'base.html'})
    response['Referrer-Policy'] = 'same-origin'
    return response


@never_cache
@require_http_methods(['GET', 'POST'])
def position(request, slug):
    book = allowed_book(request, slug)
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Для синхронизации позиции войдите в аккаунт.'}, status=401)
    video = payload(book)
    revision = video['revision']
    if request.method == 'GET':
        row = VideoPosition.objects.filter(user=request.user, book=book, revision=revision).first()
        return JsonResponse({'account': str(request.user.pk), 'revision': revision,
            'position': row.position if row else 0, 'updated_at': row.updated_at.isoformat() if row else ''})
    if video['state'] != 'ready':
        return JsonResponse({'error': 'Видео ещё не готово.'}, status=409)
    try:
        data = json.loads(request.body)
        if str(data['account']) != str(request.user.pk):
            return JsonResponse({'error': 'Аккаунт изменился. Обновите страницу.'}, status=403)
        if data['revision'] != revision:
            return JsonResponse({'error': 'Версия видео изменилась.'}, status=409)
        seconds, duration = float(data['position']), float(data['duration'])
        client, sequence = data['client_id'], data['sequence']
        if not (math.isfinite(seconds) and math.isfinite(duration) and 0 <= seconds <= duration <= 86400):
            raise ValueError
        if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client): raise ValueError
        if type(sequence) is not int or not 1 <= sequence <= 2**53-1: raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, OverflowError):
        return JsonResponse({'error': 'Некорректная позиция видео.'}, status=400)
    row, _ = VideoPosition.objects.get_or_create(user=request.user, book=book,
        defaults={'revision': revision, 'client_id': client})
    # A single conditional write also avoids SQLite read-to-write lock upgrades.
    newer = ~Q(revision=revision) | ~Q(client_id=client) | Q(sequence__lt=sequence)
    VideoPosition.objects.filter(pk=row.pk).filter(newer).update(
        revision=revision, position=seconds, client_id=client, sequence=sequence, updated_at=timezone.now())
    row.refresh_from_db()
    return JsonResponse({'account': str(request.user.pk), 'position': row.position})


@never_cache
@require_GET
def status(request, slug):
    return JsonResponse(payload(allowed_book(request, slug)))


@never_cache
@require_POST
def create(request, slug):
    book = allowed_book(request, slug)
    if not can_create_video(request, book):
        if book.visibility == 'public':
            return HttpResponse('Создавать видео публичных книг может только администратор.', status=403)
        raise Http404
    try:
        delay = int(request.POST.get('delay', '15'))
        if not 3 <= delay <= 300: raise ValueError
    except (TypeError, ValueError):
        return HttpResponse('Укажите время без озвучки от 3 до 300 секунд.', status=400)
    queue_video(book, delay)
    result = payload(book)
    return JsonResponse(result, status=200 if result['state'] == 'ready' else 202)


@never_cache
@require_http_methods(['GET', 'HEAD'])
def file(request, slug):
    from .views import stream_upload
    book = allowed_book(request, slug)
    job, ready = current_video(book)
    if not ready: raise Http404
    return stream_upload(request, job.file, mime='video/mp4', filename=book.slug + '.mp4',
                         attachment=request.GET.get('download') == '1')
