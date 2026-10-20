from types import SimpleNamespace

def book_sheets(book, pages):
    """One shared sequence for reader, PDF content filtering and complete audiobook."""
    selected = {book.cover_page_id, book.ending_page_id} - {None}
    body = [page for page in pages if page.pk not in selected]
    cover_source, end_source = book.cover_page, book.ending_page
    cover_image = (cover_source.illustration if cover_source else book.cover) or next((p.illustration for p in body if p.illustration), None)
    end_image = end_source.illustration if end_source else book.ending_image
    cover = SimpleNamespace(pk='cover', kind='cover', title='Обложка', display_title=book.title,
                            illustration=cover_image, text=book.age_label,
                            audio=book.cover_audio,
                            pdf_full_page=book.cover_full_page or bool(cover_source and cover_source.pdf_full_page))
    ending = SimpleNamespace(pk='ending', kind='ending', title='Последняя страница', display_title=book.ending_title,
                             illustration=end_image, text=book.ending_text,
                             audio=book.ending_audio,
                             pdf_full_page=book.ending_full_page or bool(end_source and end_source.pdf_full_page))
    for page in body: page.kind = 'story'
    return [cover, *body, ending]

def reader_spread(book, sheets, number):
    """Logical story spreads; pair adjacent imported facsimile leaves."""
    groups = []
    index = 0
    while index < len(sheets):
        sheet = sheets[index]
        size = 1
        if (book.pdf_layout == 'alternating' and sheet.kind == 'story' and sheet.pdf_full_page
                and index + 1 < len(sheets) and sheets[index + 1].kind == 'story'
                and sheets[index + 1].pdf_full_page):
            size = 2
        groups.append((index + 1, sheets[index:index + size]))
        index += size
    for group_index, (start, pages) in enumerate(groups):
        if start <= number < start + len(pages):
            split_story = (book.pdf_layout == 'alternating' and len(pages) == 1
                           and pages[0].kind == 'story' and not pages[0].pdf_full_page
                           and pages[0].illustration and pages[0].text.strip())
            return {'pages': pages, 'split_story': bool(split_story),
                    'previous': groups[group_index - 1][0] if group_index else 0,
                    'next': groups[group_index + 1][0] if group_index + 1 < len(groups) else 0,
                    'label': str(start) if len(pages) == 1 else f'{start}–{start + len(pages) - 1}'}

def project_qr(book=None, kind='book'):
    from django.conf import settings
    from .qr import qr_svg
    url = (book.video_share_url if kind == 'video' else book.share_url) if book else settings.SITE_URL.rstrip('/')+'/'
    return qr_svg(url, kind, book.ending_qr_color if book else '#000000')


def ending_qrs(book):
    codes = []
    if book.ending_show_qr:
        codes.append({'kind': 'book', 'url': book.share_url, 'label': 'Читать книгу', 'svg': project_qr(book)})
    if book.ending_show_video_qr:
        codes.append({'kind': 'video', 'url': book.video_share_url, 'label': 'Смотреть видео', 'svg': project_qr(book, 'video')})
    return codes
