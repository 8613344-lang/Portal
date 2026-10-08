import re
import uuid
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from .forms import OrderForm, PageAudioForm, RegistrationForm
from .models import Book, BookPage, Notification, NotificationRecipient, Order

def home(request):
    examples = Book.objects.filter(status='published', visibility='public')[:3]
    return render(request, 'library/home.html', {'examples': examples})

def catalog(request):
    query = request.GET.get('q', '').strip()[:100]
    books = Book.objects.filter(status='published', visibility='public')
    if query:
        books = books.filter(Q(title__icontains=query) | Q(description__icontains=query))
    return render(request, 'library/catalog.html', {'page_obj': Paginator(books, 18).get_page(request.GET.get('page')), 'query': query})

@login_required
@never_cache
def account(request):
    books = Book.objects.filter(grants__user=request.user, status='published').distinct()
    return render(request, 'library/account.html', {'page_obj': Paginator(books, 18).get_page(request.GET.get('page')), 'orders': Order.objects.filter(user=request.user)[:50]})

@never_cache
@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated: return redirect('account')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('account')
    return render(request, 'registration/register.html', {'form': form})

@never_cache
def reader(request, slug):
    book = get_object_or_404(Book.objects.visible_to(request.user), slug=slug)
    pages = list(book.pages.all())
    try: number = int(request.GET.get('page', '1'))
    except ValueError: number = 1
    number = max(1, min(number, len(pages)))
    current = pages[number - 1] if pages else None
    can_edit = request.user.is_authenticated and request.user.is_staff and request.user.has_perm('library.change_bookpage')
    return render(request, 'library/reader.html', {'book': book, 'pages': pages, 'current': current, 'number': number, 'total': len(pages), 'previous': number - 1, 'next': number + 1, 'can_edit': can_edit, 'audio_form': PageAudioForm(), 'audio_count': sum(bool(p.audio) for p in pages)})

@never_cache
@require_http_methods(['GET'])
def export_book(request, slug, format):
    from .exports import ExportError, book_pdf, book_mp3, export_slots
    book = get_object_or_404(Book.objects.visible_to(request.user), slug=slug)
    if format not in ('pdf', 'mp3'): raise Http404
    if not export_slots.acquire(blocking=False):
        response = HttpResponse('Выгрузки заняты. Повторите попытку через несколько секунд.', status=503, content_type='text/plain; charset=utf-8')
        response['Retry-After'] = '10'
        return response
    try:
        pages = list(book.pages.all())
        output = book_pdf(book, pages) if format == 'pdf' else book_mp3(book, pages)
        response = FileResponse(output, as_attachment=True, filename=f'{book.slug}.{format}',
                                content_type='application/pdf' if format == 'pdf' else 'audio/mpeg')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except ExportError as error:
        return HttpResponse(str(error), status=422, content_type='text/plain; charset=utf-8')
    except (OSError, ValueError):
        return HttpResponse('Не удалось прочитать файлы книги. Обратитесь к администратору.', status=422, content_type='text/plain; charset=utf-8')
    finally:
        export_slots.release()

@require_POST
@login_required
@never_cache
def page_audio(request, pk):
    if not request.user.is_staff or not request.user.has_perm('library.change_bookpage'):
        raise Http404
    page = get_object_or_404(BookPage, pk=pk)
    if request.POST.get('action') == 'remove':
        page.audio = ''
        page.save(update_fields=['audio'])
        messages.success(request, 'Запись удалена со страницы.')
    else:
        form = PageAudioForm(request.POST, request.FILES)
        if form.is_valid():
            page.audio = form.cleaned_data['audio']
            page.save(update_fields=['audio'])
            messages.success(request, 'Запись сохранена на сервере.')
        else:
            messages.error(request, ' '.join(str(error) for errors in form.errors.values() for error in errors))
    index = list(page.book.pages.values_list('pk', flat=True)).index(page.pk) + 1
    from django.urls import reverse
    return redirect(reverse('reader', args=[page.book.slug]) + f'?page={index}#page-audio')

@never_cache
@require_http_methods(['GET', 'POST'])
def order_create(request):
    tokens = request.session.get('order_tokens', [])
    if request.method == 'POST':
        token = request.POST.get('submission_key', '')
        if token not in tokens:
            return HttpResponse('Форма устарела. Откройте страницу заказа ещё раз.', status=400)
        existing = Order.objects.filter(submission_key=token).first()
        if existing:
            request.session['last_order'] = str(existing.id)
            return redirect('order-success')
        form = OrderForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                order = form.save(commit=False)
                order.submission_key = uuid.UUID(token)
                order.user = request.user if request.user.is_authenticated else None
                defaults = {field.name: getattr(order, field.name) for field in Order._meta.fields if field.name not in ['id', 'submission_key', 'created_at']}
                order, created = Order.objects.get_or_create(submission_key=order.submission_key, defaults=defaults)
                if created:
                    for recipient in NotificationRecipient.objects.filter(active=True).select_related('employee'):
                        if recipient.allowed():
                            Notification.objects.create(order=order, recipient=recipient, destination=recipient.destination, channel=recipient.channel)
            request.session['last_order'] = str(order.id)
            return redirect('order-success')
    else:
        token = str(uuid.uuid4())
        request.session['order_tokens'] = (tokens + [token])[-20:]
        form = OrderForm(initial={'parent_name': request.user.first_name, 'email': request.user.email} if request.user.is_authenticated else None)
    return render(request, 'library/order.html', {'form': form, 'submission_key': token})

@never_cache
def order_success(request):
    order_id = request.session.get('last_order')
    if not order_id: return redirect('order')
    return render(request, 'library/order_success.html', {'order_id': order_id})

def privacy(request): return render(request, 'library/privacy.html')

def health(request):
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return HttpResponse('ok', content_type='text/plain')

def ranged_chunks(file, start, length):
    try:
        file.seek(start)
        while length > 0:
            chunk = file.read(min(64 * 1024, length))
            if not chunk: break
            length -= len(chunk)
            yield chunk
    finally: file.close()

@never_cache
@require_http_methods(['GET', 'HEAD'])
def private_file(request, path):
    file_field = None
    book = Book.objects.filter(Q(cover=path) | Q(background_image=path)).first()
    page = BookPage.objects.filter(Q(illustration=path) | Q(audio=path)).select_related('book').first()
    if page:
        book = page.book
        file_field = page.audio if page.audio.name == path else page.illustration
    elif book:
        file_field = book.background_image if book.background_image.name == path else book.cover
    if book:
        if not Book.objects.visible_to(request.user).filter(pk=book.pk).exists(): raise Http404
    else:
        order = Order.objects.filter(photo=path).first()
        if not order or not request.user.is_authenticated or not request.user.is_staff or not request.user.has_perm('library.view_order'):
            raise Http404
        file_field = order.photo
    if not file_field or not file_field.storage.exists(file_field.name): raise Http404
    mime = 'audio/mpeg' if path.lower().endswith('.mp3') else 'audio/wav' if path.lower().endswith('.wav') else 'image/jpeg'
    size = file_field.size
    range_header = request.headers.get('Range', '')
    start, end, status = 0, size - 1, 200
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        if not match or not any(match.groups()):
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response
        left, right = match.groups()
        if left:
            start = int(left)
            end = min(int(right), size - 1) if right else size - 1
        else:
            start = max(0, size - int(right))
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response
        status = 206
    length = end - start + 1
    if request.method == 'HEAD': response = HttpResponse(content_type=mime, status=status)
    else:
        file = file_field.open('rb')
        response = StreamingHttpResponse(ranged_chunks(file, start, length), content_type=mime, status=status)
    response['Content-Length'] = str(length)
    response['Accept-Ranges'] = 'bytes'
    response['Content-Disposition'] = 'inline; filename="page-audio' + ('.mp3"' if mime == 'audio/mpeg' else '.wav"' if mime == 'audio/wav' else '.jpg"')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    if status == 206: response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response
