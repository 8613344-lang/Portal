"""Private, on-demand book exports; no public export directory or URLs."""
import shutil
import subprocess
import tempfile
import threading
import time
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings


class ExportError(Exception):
    pass


export_slots = threading.BoundedSemaphore(2)
font_lock = threading.Lock()


def book_pdf(book, pages):
    """A4 combined/alternating layouts; imported PDF leaves preserve their source."""
    from .sheets import book_sheets
    sequence = book_sheets(book, pages)
    cover_sheet, ending_sheet = sequence[0], sequence[-1]
    pages = sequence[1:-1]
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph
    from reportlab.graphics import renderPDF
    from .qr import qr_drawing

    with font_lock:
        if 'BookSerif' not in pdfmetrics.getRegisteredFontNames():
            root = settings.BASE_DIR / 'assets' / 'fonts'
            pdfmetrics.registerFont(TTFont('BookSerif', str(root / 'DejaVuSerif.ttf')))
            pdfmetrics.registerFont(TTFont('BookSerifBold', str(root / 'DejaVuSerif-Bold.ttf')))
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    width, height = A4
    margin = 20 * mm
    content_width = width - 2 * margin
    ink = colors.HexColor('#304b40')
    canvas = Canvas(output, pagesize=A4, pageCompression=1)
    canvas.setTitle(book.title)
    canvas.setAuthor('Книги рядом')

    def text(value):
        glyphs = pdfmetrics.getFont('BookSerif').face.charToGlyph
        # Ignore unsupported decorative emoji instead of printing replacement boxes/stars.
        return escape(''.join(c for c in value if ord(c) in glyphs or c in '\n\t'))

    def sheet_base():
        palette = {'paper':'#fcf5e8', 'white':'#ffffff', 'mint':'#edf5ed', 'sky':'#edf4fb', 'rose':'#fbefef'}
        canvas.setFillColor(colors.HexColor(palette.get(book.background_theme, '#fcf5e8')))
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        if book.background_theme == 'custom' and book.background_image:
            full_picture(book.background_image)

    def background(number):
        sheet_base()
        canvas.setStrokeColor(colors.HexColor('#dfd0b5'))
        canvas.roundRect(10*mm, 10*mm, width-20*mm, height-20*mm, 4*mm, stroke=1, fill=0)
        canvas.setFillColor(colors.HexColor('#e6eddf'))
        canvas.circle(width-14*mm, height-14*mm, 8*mm, stroke=0, fill=1)
        canvas.setFont('BookSerif', 9)
        canvas.setFillColor(ink)
        if number is not None: canvas.drawCentredString(width/2, 13*mm, str(number))

    def fit_paragraph(value, available_width, available_height, maximum=14, minimum=9,
                      bold=False, centered=False, identifier=''):
        # Measure and draw the SAME Paragraph, width and leading; no frame pagination.
        low, high = minimum, maximum
        chosen = None
        for _ in range(14):
            size = maximum if _ == 0 else (low+high)/2
            style = ParagraphStyle('Fit', fontName='BookSerifBold' if bold else 'BookSerif',
                                   fontSize=size, leading=size*1.3, textColor=ink,
                                   alignment=TA_CENTER if centered else 0,
                                   splitLongWords=True, allowWidows=1, allowOrphans=1)
            paragraph = Paragraph(value, style)
            _, used_height = paragraph.wrap(available_width, available_height)
            if used_height <= available_height:
                chosen = (paragraph, used_height)
                low = size
                if size == maximum: break
            else:
                high = size
            if high-low < 0.01: break
        if chosen is None:
            style = ParagraphStyle('Minimum', fontName='BookSerifBold' if bold else 'BookSerif',
                                   fontSize=minimum, leading=minimum*1.3, textColor=ink,
                                   alignment=TA_CENTER if centered else 0)
            paragraph = Paragraph(value, style)
            _, used_height = paragraph.wrap(available_width, available_height)
            if used_height > available_height:
                raise ExportError(f'Страница «{identifier or book.title}» содержит слишком много текста для одного листа. Сократите текст этой страницы и повторите выгрузку.')
            chosen = (paragraph, used_height)
        return chosen

    def draw_paragraph(value, top, available_height, **options):
        paragraph, used_height = fit_paragraph(value, content_width, available_height, **options)
        paragraph.drawOn(canvas, margin, top-used_height)
        return top-used_height

    def picture(field, top, box_height):
        if not field: return
        with field.open('rb') as source:
            image = ImageReader(BytesIO(source.read()))
        image_width, image_height = image.getSize()
        scale = min(content_width / image_width, box_height / image_height)
        drawn_width, drawn_height = image_width*scale, image_height*scale
        canvas.drawImage(image, (width-drawn_width)/2, top-box_height+(box_height-drawn_height)/2,
                         width=drawn_width, height=drawn_height, mask='auto')

    def full_picture(field, preserve=False):
        with field.open('rb') as source:
            image = ImageReader(BytesIO(source.read()))
        iw, ih = image.getSize()
        scale = min(width/iw, height/ih) if preserve else max(width/iw, height/ih)
        dw, dh = iw*scale, ih*scale
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(0, 0, width, height)
        canvas.clipPath(path, stroke=0)
        canvas.drawImage(image, (width-dw)/2, (height-dh)/2, width=dw, height=dh, mask='auto')
        canvas.restoreState()

    try:
        if cover_sheet.pdf_full_page and cover_sheet.illustration:
            sheet_base()
            full_picture(cover_sheet.illustration, preserve=True)
        else:
            background(None)
            draw_paragraph('КНИГИ РЯДОМ', height-28*mm, 10*mm, maximum=12, centered=True)
            draw_paragraph(text(book.title), height-45*mm, 45*mm, maximum=30, minimum=16, bold=True, centered=True)
            picture(cover_sheet.illustration, height-100*mm, 140*mm)
            draw_paragraph(text(book.age_label), 42*mm, 15*mm, maximum=13, centered=True)
        canvas.showPage()
        leaf_number = 2
        for index, page in enumerate(pages, 1):
            if page.pdf_full_page and page.illustration:
                sheet_base()
                full_picture(page.illustration, preserve=True)
                canvas.showPage()
                leaf_number += 1
                continue
            alternating = book.pdf_layout == 'alternating'
            if alternating and page.illustration:
                sheet_base()
                full_picture(page.illustration)
                canvas.showPage()
                leaf_number += 1
                if not page.text.strip(): continue
            background(leaf_number)
            canvas.setFont('BookSerif', 10)
            canvas.setFillColor(ink)
            canvas.drawString(margin, height-23*mm, f'История {index}')
            title_bottom = draw_paragraph(text(page.title), height-30*mm, 24*mm,
                                          maximum=23, minimum=13, bold=True, identifier=page.title)
            content_top = title_bottom-6*mm
            if page.illustration and not alternating:
                picture(page.illustration, content_top, 112*mm)
                content_top -= 119*mm
            value = '<br/><br/>'.join(text(p).replace('\n','<br/>') for p in page.text.split('\n\n') if p.strip())
            draw_paragraph(value, content_top, content_top-24*mm, maximum=14, minimum=9,
                           identifier=page.title)
            canvas.showPage()
            leaf_number += 1
        end_picture = ending_sheet.illustration
        if end_picture and ending_sheet.pdf_full_page:
            sheet_base()
            full_picture(end_picture, preserve=True)
        else:
            background(leaf_number)
            if end_picture: full_picture(end_picture, preserve=True)
        ink = colors.HexColor(book.ending_text_color)
        codes = []
        if book.ending_show_qr: codes.append(('book', book.share_url, 'Читать книгу'))
        if book.ending_show_video_qr: codes.append(('video', book.video_share_url, 'Смотреть видео'))
        size, padding, label_height, gap = 44*mm, 6*mm, (8*mm if book.ending_show_text else 0), 8*mm
        paragraph, used = None, 0
        if book.ending_show_text and (book.ending_title or book.ending_text):
            value = ('<b>'+text(book.ending_title)+'</b><br/><br/>' if book.ending_title else '') + text(book.ending_text).replace('\n','<br/>')
            pdfmetrics.registerFontFamily('BookSerif', normal='BookSerif', bold='BookSerifBold')
            text_width = content_width - 2*padding - (size+gap if len(codes) == 1 else 0)
            paragraph, used = fit_paragraph(value, text_width, 70*mm, maximum=14, minimum=9,
                                            centered=len(codes) != 1, identifier='Последняя страница')
        qr_height = size+label_height if codes else 0
        # Align text with the printed modules, below the code's white quiet zone.
        text_inset = 5*mm if len(codes) == 1 else 0
        block_height = (qr_height+gap+used if paragraph else qr_height) if len(codes) == 2 else max(qr_height, used+text_inset)
        panel_height = max(44*mm, block_height) + 2*padding
        panel_bottom = 22*mm
        top = panel_bottom+panel_height-padding
        if end_picture and (paragraph or codes):
            canvas.saveState()
            canvas.setFillColor(colors.HexColor(book.ending_panel_color))
            canvas.setFillAlpha((100-book.ending_panel_transparency)/100)
            canvas.roundRect(margin, panel_bottom, content_width, panel_height, 4*mm, fill=1,stroke=0)
            canvas.restoreState()
        if paragraph:
            text_top = top-qr_height-gap if len(codes) == 2 else top-text_inset
            paragraph.drawOn(canvas, margin+padding, text_top-used)
        for index, (kind, url, label) in enumerate(codes):
            x = (width-2*size-12*mm)/2+index*(size+12*mm) if len(codes) == 2 else width-margin-padding-size
            y = top-size
            renderPDF.draw(qr_drawing(url, size, kind, book.ending_qr_color), canvas, x, y)
            canvas.linkURL(url,(x,y,x+size,y+size),relative=0)
            if book.ending_show_text:
                canvas.setFillColor(ink)
                canvas.setFont('BookSerif', 10)
                canvas.drawCentredString(x+size/2, y-5*mm, label)
        canvas.showPage()
        canvas.save()
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise



def book_mp3(book, pages):
    """Decode to a common PCM format before joining: handles differing MP3/WAV rates."""
    import imageio_ffmpeg
    from .sheets import book_sheets
    if not pages or getattr(pages[0], "kind", None) != "cover": pages = book_sheets(book, pages)
    recordings = [page for page in pages if page.audio]
    if not recordings: raise ExportError('В книге пока нет аудиозаписей.')
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    deadline = time.monotonic() + 100
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)

    def run(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise ExportError('Сборка заняла слишком много времени. Попробуйте книгу меньшего размера.')
        try:
            subprocess.run([executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                            '-threads', '1', *arguments], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=remaining,
                           creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        except (subprocess.SubprocessError, OSError) as error:
            raise ExportError('Не удалось собрать MP3. Проверьте записи страниц и повторите попытку.') from error
    try:
        with tempfile.TemporaryDirectory(prefix='book-audio-') as directory:
            root = Path(directory)
            total = 0
            names = []
            for index, page in enumerate(recordings):
                source = root / f'source-{index}{Path(page.audio.name).suffix.lower()}'
                with page.audio.open('rb') as stream, source.open('wb') as target:
                    shutil.copyfileobj(stream, target)
                target = root / f'part-{index}.wav'
                run(['-protocol_whitelist', 'file,pipe', '-i', str(source), '-vn', '-map_metadata', '-1',
                     '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le', '-fs', '1073741824', str(target)])
                total += target.stat().st_size
                if total >= 1024**3: raise ExportError('Записи слишком велики для одной выгрузки (предел 1 ГБ промежуточного аудио).')
                source.unlink()
                names.append(f"file '{target.name}'")
            manifest = root / 'list.txt'
            manifest.write_text('\n'.join(names), encoding='ascii')
            final = root / 'book.mp3'
            run(['-f', 'concat', '-safe', '1', '-protocol_whitelist', 'file,pipe', '-i', str(manifest),
                 '-vn', '-c:a', 'libmp3lame', '-b:a', '192k', '-map_metadata', '-1', str(final)])
            with final.open('rb') as stream: shutil.copyfileobj(stream, output)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise
