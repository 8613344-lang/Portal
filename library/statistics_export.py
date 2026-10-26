import csv

from django.http import StreamingHttpResponse
from django.utils import timezone


def cell(value):
    text = str(value or '')
    # Spreadsheet apps must treat visitor-supplied fields as text, not formulas.
    return "'"+text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text


class Echo:
    def write(self, value): return value


def visits_csv(queryset):
    writer = csv.writer(Echo(), delimiter=';', lineterminator='\r\n')
    def rows():
        yield '\ufeff'
        yield writer.writerow(['ID', 'Время', 'Событие', 'Страница сайта', 'Книга', 'Страница книги',
            'Пользователь', 'IP', 'Город (предположительно)', 'Регион (предположительно)',
            'Страна (предположительно)', 'Источник GeoIP', 'Браузер', 'Версия браузера',
            'ОС', 'Устройство', 'Предположительно робот', 'Источник перехода', 'User-Agent'])
        for row in queryset.select_related('book','user').iterator(chunk_size=1000):
            yield writer.writerow([row.pk, timezone.localtime(row.occurred_at).isoformat(),
                cell(row.get_kind_display()), cell(row.path), cell(row.book.title if row.book_id else ''),
                row.book_page or '', cell(row.user.email or row.user.username if row.user_id else 'Гость'),
                cell(row.ip_address), cell(row.city), cell(row.region), cell(row.country), cell(row.geo_source),
                cell(row.browser), cell(row.browser_version), cell(row.os), cell(row.device),
                'Да' if row.is_bot else 'Нет', cell(row.referrer), cell(row.user_agent)])
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="visits-' + timezone.localdate().isoformat() + '.csv"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
