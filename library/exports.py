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
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics.barcode.qr import QrCodeWidget

    with font_lock:
        if 'BookSerif' not in pdfmetrics.getRegisteredFontNames():
            root = settings.BASE_DIR / 'assets' / 'fonts'
            pdfmetrics.registerFont(TTFont('BookSerif', str(root / 'DejaVuSerif.ttf')))
            pdfmetrics.registerFont(TTFont('BookSerifBold', str(root / 'DejaVuSerif-Bold.ttf')))
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    width, height = A5
    ink = colors.HexColor('#304b40')
    body = ParagraphStyle('Story', fontName='BookSerif', fontSize=12, leading=19,
                          textColor=ink, spaceAfter=10, splitLongWords=True)
    heading = ParagraphStyle('Heading', parent=body, fontName='BookSerifBold',
                             fontSize=20, leading=27, spaceAfter=15, keepWithNext=True)
    center = ParagraphStyle('Center', parent=body, alignment=TA_CENTER)
    cover_title = ParagraphStyle('Cover', parent=heading, alignment=TA_CENTER,
                                fontSize=25, leading=33, keepWithNext=False)
    doc = SimpleDocTemplate(output, pagesize=A5, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=20*mm, bottomMargin=20*mm, title=book.title,
                            author='Книги рядом', pageCompression=1)
    story = []

    def text(value):
        # Decorative emoji absent from the embedded font must not become black boxes.
        glyphs = pdfmetrics.getFont('BookSerif').face.charToGlyph
        value = ''.join(character if ord(character) in glyphs or character in '\n\t' else
                        '' if ord(character) in (0xFE0F, 0x200D) else '*' for character in value)
        return escape(value)

    def picture(field, max_height):
        if not field: return None
        with field.open('rb') as source:
            data = BytesIO(source.read())
        image = Image(data)
        scale = min(doc.width / image.imageWidth, max_height / image.imageHeight)
        image.drawWidth = image.imageWidth * scale
        image.drawHeight = image.imageHeight * scale
        image.hAlign = 'CENTER'
        return image

    story += [Spacer(1, 12*mm), Paragraph('КНИГИ РЯДОМ', center), Spacer(1, 8*mm),
              Paragraph(text(book.title), cover_title), Spacer(1, 6*mm)]
    cover = picture(book.cover or next((p.illustration for p in pages if p.illustration), None), 90*mm)
    if cover: story += [cover, Spacer(1, 6*mm)]
    story.append(Paragraph(text(book.age_label), center))
    for index, page in enumerate(pages, 1):
        label = Paragraph(f'История {index}', body)
        title = Paragraph(text(page.title), heading)
        paragraphs = [text(p).replace('\n', '<br/>') for p in page.text.split('\n\n') if p.strip()]
        # Prefer one printed leaf per source page, without cropping long stories.
        for font_size, image_height in ((12, 65), (11, 50), (10.5, 38), (10.5, 30), (10, 24)):
            style = ParagraphStyle('PageStory', parent=body, fontSize=font_size,
                                   leading=font_size*1.45, spaceAfter=5)
            image = picture(page.illustration, image_height*mm)
            content = [label, title]
            if image: content += [image, Spacer(1, 5*mm)]
            content += [Paragraph(p, style) for p in paragraphs]
            used = sum(item.wrap(doc.width-12, doc.height)[1] + item.getSpaceAfter() +
                       item.getSpaceBefore() for item in content)
            if used <= doc.height-60: break
        story += [PageBreak(), *content]
    url = settings.SITE_URL.rstrip('/') + '/'
    qr = QrCodeWidget(url)
    bounds = qr.getBounds()
    size = 45 * mm
    drawing = Drawing(size, size, transform=[size/(bounds[2]-bounds[0]), 0, 0,
                                           size/(bounds[3]-bounds[1]), 0, 0])
    drawing.add(qr)
    drawing.hAlign = 'CENTER'
    story += [PageBreak(), Spacer(1, 20*mm), Paragraph('Продолжение начинается здесь', cover_title),
              Spacer(1, 10*mm), Paragraph('Новые истории и персональные книги для вашего ребёнка.', center),
              Spacer(1, 10*mm), drawing, Spacer(1, 8*mm), Paragraph(escape(url), center)]

    def background(canvas, document):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor('#fcf5e8'))
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.setStrokeColor(colors.HexColor('#dfd0b5'))
        canvas.roundRect(9*mm, 9*mm, width-18*mm, height-18*mm, 4*mm, stroke=1, fill=0)
        canvas.setFillColor(colors.HexColor('#e6eddf'))
        canvas.circle(width-13*mm, height-13*mm, 7*mm, stroke=0, fill=1)
        canvas.setFont('BookSerif', 9)
        canvas.setFillColor(ink)
        canvas.drawCentredString(width/2, 12*mm, str(document.page))
        canvas.restoreState()
    try:
        doc.build(story, onFirstPage=background, onLaterPages=background)
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise


def book_mp3(book, pages):
    """Decode to a common PCM format before joining: handles differing MP3/WAV rates."""
    import imageio_ffmpeg
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
