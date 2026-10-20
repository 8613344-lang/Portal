from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import Book
from .videos import current_video, queue_video


def can_watch_video(request, book):
    from .views import can_read_book
    token = request.session.get('shared_videos', {}).get(str(book.pk))
    return token == str(book.video_share_token) or can_read_book(request, book)


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
    app = request.GET.get('app') == '1'
    response = render(request, 'library/video.html', {'book': book, 'video': payload(book), 'app_mode': app,
        'can_manage_video': can_read_book(request, book),
        'device': 'tablet' if request.GET.get('device') == 'tablet' else 'phone',
        'base_template': 'app_base.html' if app else 'base.html'})
    response['Referrer-Policy'] = 'same-origin'
    return response


@never_cache
@require_GET
def status(request, slug):
    return JsonResponse(payload(allowed_book(request, slug)))


@never_cache
@require_POST
def create(request, slug):
    from .views import can_read_book
    book = allowed_book(request, slug)
    if not can_read_book(request, book): raise Http404
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
