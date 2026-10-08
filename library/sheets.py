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

def project_qr():
    from django.conf import settings
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.barcode.qr import QrCodeWidget
    qr = QrCodeWidget(settings.SITE_URL.rstrip('/')+'/')
    bounds = qr.getBounds()
    size = 160
    drawing = Drawing(size,size,transform=[size/(bounds[2]-bounds[0]),0,0,size/(bounds[3]-bounds[1]),0,0])
    drawing.add(qr)
    svg = drawing.asString('svg')
    if isinstance(svg, bytes):
        svg = svg.decode('utf-8')
    return svg[svg.index('<svg'):svg.rindex('</svg>') + 6]
