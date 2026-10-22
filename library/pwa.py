"""Home-screen installation; only a generic offline page is cached."""
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_safe


@require_safe
def manifest(request):
    response = JsonResponse({
        'id': '/', 'name': 'Книги рядом', 'short_name': 'Книги рядом', 'lang': 'ru',
        'description': 'Персональные истории, иллюстрации и озвучка для вашего ребёнка.',
        'start_url': reverse('web-shelf'), 'scope': '/', 'display': 'standalone',
        'background_color': '#fbf7ee', 'theme_color': '#fbf7ee',
        'icons': [
            {'src': static(f'pwa/icon-{size}.png'), 'sizes': f'{size}x{size}',
             'type': 'image/png', 'purpose': 'any'} for size in (192, 512)
        ] + [{'src': static('pwa/icon-maskable-512.png'), 'sizes': '512x512',
              'type': 'image/png', 'purpose': 'maskable'}],
        'shortcuts': [
            {'name': 'Моя книжная полка', 'url': reverse('web-shelf')},
            {'name': 'Создать книгу', 'url': reverse('order')},
        ],
    }, json_dumps_params={'ensure_ascii': False}, content_type='application/manifest+json')
    response['Cache-Control'] = 'public, max-age=0, must-revalidate'
    return response


@require_safe
def service_worker(request):
    response = render(request, 'pwa/service-worker.js', content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    return response


@require_safe
def install(request):
    return render(request, 'pwa/install.html')


@require_safe
def offline(request):
    # A standalone template: no account, CSRF token, book or order data.
    response = render(request, 'pwa/offline.html')
    response['Cache-Control'] = 'public, max-age=0, must-revalidate'
    return response
