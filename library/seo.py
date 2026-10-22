"""Public search pages and a default-deny indexing policy for everything else."""
import json
import math
from xml.etree.ElementTree import Element, SubElement, tostring

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.http import require_safe

from .models import Book
from .images import image_url

BRAND = 'Книги рядом'
HOME_TITLE = 'Персональная книга для ребёнка на заказ — Книги рядом'
HOME_DESCRIPTION = ('Именная сказка, в которой ваш ребёнок — главный герой. '
                    'История по вашим пожеланиям, иллюстрации, озвучка, PDF и видео. Фото — по желанию.')
FAQ = [
    ('Что такое персональная книга для ребёнка?',
     'Это история с именем, увлечениями и характером вашего ребёнка. Можно рассказать о его мечте, семейном приключении или важном событии.'),
    ('Обязательно ли прикладывать фото ребёнка?',
     'Нет. Фото можно приложить по желанию вместе с описанием ребёнка и пожеланиями к иллюстрациям.'),
    ('Можно ли заказать сказку на день рождения или о детском саде?',
     'Да. В заявке можно описать день рождения, первый день в детском саду, новую дружбу, поездку к близким или другую важную для семьи историю.'),
    ('В каком формате можно читать и слушать книгу?',
     'Книга открывается на сайте с телефона, планшета или компьютера. Доступны PDF и видео MP4; если добавлены записи страниц, их можно слушать и сохранить общей аудиокнигой MP3.'),
    ('Кто сможет увидеть персональную книгу?',
     'Закрытую книгу видят пользователи, которым выдан доступ, и обладатели её прямой ссылки. Отдельная ссылка на видео открывает только MP4. Публичные примеры находятся в разделе «Истории наших читателей».'),
    ('Сколько стоит создание книги и как оформить заказ?',
     'Оставьте заявку: опишите ребёнка, событие и пожелания. Стоимость, сроки и состав книги обсуждаются после заявки. Оплата при отправке формы не требуется.'),
]


def absolute(path):
    return settings.SITE_URL.rstrip('/') + path


def public_books():
    # Never use visible_to(): staff privileges and QR grants must not affect SEO.
    return Book.objects.filter(status='published', visibility='public')


def clean_text(value, limit=170):
    value = ' '.join(strip_tags(value).split())
    return value if len(value) <= limit else value[:limit-1].rsplit(' ', 1)[0] + '…'


def jsonld(data):
    # Book titles/descriptions may contain HTML or </script>. Keep them as data.
    return json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


def set_metadata(request, title, description, path, *, indexable=True, image=None, graph=None):
    indexable = (indexable and request.method in ('GET', 'HEAD')
                 and request.GET.get('app') != '1' and request.GET.get('shelf') != '1')
    request.search_metadata = {
        'title': title, 'description': clean_text(description), 'canonical': absolute(path),
        'indexable': indexable, 'robots': 'index, follow' if indexable else 'noindex, follow',
        'image': absolute(image or static('images/web/hero-1440.webp')),
        'structured_data': jsonld({'@context': 'https://schema.org', '@graph': graph}) if graph and indexable else '',
    }


def metadata_context(request):
    return {'google_site_verification': settings.GOOGLE_SITE_VERIFICATION,
            'seo': getattr(request, 'search_metadata', {
        'title': BRAND, 'description': HOME_DESCRIPTION, 'indexable': False,
        'robots': 'noindex, nofollow', 'canonical': '', 'structured_data': '',
    })}


class SearchVisibilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith(settings.STATIC_URL):
            return response
        if getattr(request, 'search_discovery', False) and response.status_code == 200:
            return response
        meta = getattr(request, 'search_metadata', {})
        if response.status_code == 200 and request.method in ('GET', 'HEAD') and meta:
            response['X-Robots-Tag'] = meta['robots']
        else:
            response['X-Robots-Tag'] = 'noindex, nofollow'
        return response


def home_metadata(request):
    set_metadata(request, HOME_TITLE, HOME_DESCRIPTION, reverse('home'), graph=[
        {'@type': 'WebSite', '@id': absolute('/#website'), 'name': BRAND,
         'alternateName': 'Qicko', 'url': absolute('/'), 'inLanguage': 'ru'},
        {'@type': 'Organization', '@id': absolute('/#organization'), 'name': BRAND, 'url': absolute('/')},
    ])


@require_safe
def book_service(request):
    path = reverse('book-service')
    set_metadata(request, 'Именная книга и сказка про ребёнка на заказ — Книги рядом',
        'Как заказать персональную детскую книгу: имя, характер, любимые герои и важное событие. '
        'Иллюстрации, аудиосказка, PDF и видео. Ответы на вопросы родителей.', path,
        graph=[{'@type': 'Service', 'name': 'Создание персональной книги для ребёнка',
                'serviceType': 'Персональные детские книги и сказки на заказ', 'url': absolute(path),
                'provider': {'@type': 'Organization', 'name': BRAND, 'url': absolute('/')},
                'description': HOME_DESCRIPTION}])
    return render(request, 'library/book_service.html', {'faq': FAQ})


@require_safe
def book_detail(request, slug):
    book = get_object_or_404(public_books().select_related('cover_page'), slug=slug)
    description = book.description or f'«{book.title}» — пример персональной детской книги. {book.age_label}. Читайте историю и создайте свою книгу о ребёнке.'
    path = reverse('book-detail', args=[book.slug])
    image = book.cover or (book.cover_page.illustration if book.cover_page_id else None)
    schema = {'@type': 'Book', 'name': book.title, 'description': clean_text(description, 500),
              'url': absolute(path), 'inLanguage': 'ru', 'bookFormat': 'https://schema.org/EBook',
              'publisher': {'@type': 'Organization', 'name': BRAND}}
    if image: schema['image'] = absolute(image_url(image, 1280))
    set_metadata(request, f'{book.title} — персональная детская книга | Книги рядом', description, path,
        image=image_url(image, 1280) if image else None, graph=[schema,
            {'@type': 'BreadcrumbList', 'itemListElement': [
                {'@type': 'ListItem', 'position': 1, 'name': BRAND, 'item': absolute('/')},
                {'@type': 'ListItem', 'position': 2, 'name': 'Истории наших читателей', 'item': absolute(reverse('catalog'))},
                {'@type': 'ListItem', 'position': 3, 'name': book.title, 'item': absolute(path)}]}])
    pages = book.pages.exclude(pk__in=[pk for pk in (book.cover_page_id, book.ending_page_id) if pk])
    excerpt = next((clean_text(page.text, 600) for page in pages[:5] if page.text.strip()), '')
    return render(request, 'library/book_detail.html', {'book': book, 'cover': image, 'excerpt': excerpt})


@require_safe
def robots(request):
    request.search_discovery = True
    # Crawlers must be able to fetch noindex responses. Authentication still protects data.
    return HttpResponse('User-agent: *\nAllow: /\n\nSitemap: ' + absolute(reverse('sitemap')) + '\n',
                        content_type='text/plain; charset=utf-8')


def xml_response(request, kind, paths):
    request.search_discovery = True
    root = Element(kind, xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for path in paths:
        item = SubElement(root, 'sitemap' if kind == 'sitemapindex' else 'url')
        SubElement(item, 'loc').text = absolute(path)
    response = HttpResponse(tostring(root, encoding='utf-8', xml_declaration=True),
                            content_type='application/xml; charset=utf-8')
    response['Cache-Control'] = 'no-cache'
    return response


SITEMAP_BOOKS_PER_FILE = 1000


@require_safe
def sitemap(request):
    count = public_books().count()
    paths = [reverse('sitemap-pages')] + [reverse('sitemap-books', args=[page])
        for page in range(1, math.ceil(count / SITEMAP_BOOKS_PER_FILE)+1)]
    return xml_response(request, 'sitemapindex', paths)


@require_safe
def sitemap_pages(request):
    paths = [reverse(name) for name in ('home', 'book-service', 'catalog', 'order')]
    paths += [reverse('catalog')+f'?page={page}' for page in range(2, math.ceil(public_books().count()/18)+1)]
    return xml_response(request, 'urlset', paths)


@require_safe
def sitemap_books(request, page):
    paginator = Paginator(public_books().order_by('pk').only('slug'), SITEMAP_BOOKS_PER_FILE)
    try:
        books = paginator.page(page)
    except EmptyPage:
        raise Http404
    if not books.object_list: raise Http404
    return xml_response(request, 'urlset', [reverse('book-detail', args=[book.slug]) for book in books])
