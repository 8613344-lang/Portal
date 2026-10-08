"""Render PDF locally into safe private images. No document scripts or remote services."""
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from django.core.files import File
from django.db import transaction
from .models import BookPage

pdfium_lock = threading.Lock()

class PDFImportError(Exception):
    pass

class PreparedPDF:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='book-pdf-')
        self.pages = []
    def close(self): self.directory.cleanup()

def prepare_pdf(file):
    import pypdfium2 as pdfium
    file.seek(0)
    data = file.read(100*1024*1024 + 1)
    file.seek(0)
    if len(data) > 100*1024*1024: raise PDFImportError('PDF превышает 100 МБ.')
    if not data.startswith(b'%PDF-'): raise PDFImportError('Нужен настоящий PDF-файл.')
    prepared = PreparedPDF()
    deadline = time.monotonic()+90
    try:
        if not pdfium_lock.acquire(timeout=5): raise PDFImportError('Другой PDF сейчас обрабатывается. Повторите загрузку чуть позже.')
        try:
            with pdfium.PdfDocument(data) as document:
                if not 1 <= len(document) <= 100: raise PDFImportError('Допустимо от 1 до 100 листов PDF.')
                for number in range(len(document)):
                    if time.monotonic() > deadline: raise PDFImportError('PDF обрабатывается слишком долго. Разделите файл на меньшие книги.')
                    with closing(document[number]) as page:
                        width, height = page.get_size()
                        if min(width, height) <= 0 or max(width, height) > 14400:
                            raise PDFImportError('Некорректный или слишком большой размер листа PDF.')
                        with closing(page.get_textpage()) as textpage:
                            text = textpage.get_text_range().replace('\x00', '').replace('\r\n', '\n').strip()
                        if len(text) > 100000: raise PDFImportError('Слишком большой текстовый слой листа PDF.')
                        with closing(page.render(scale=min(2, 2400/max(width, height)), draw_annots=False)) as bitmap:
                            image = bitmap.to_pil().convert('RGB')
                            path = Path(prepared.directory.name) / f'{number+1}.jpg'
                            image.save(path, 'JPEG', quality=92)
                            image.close()
                        prepared.pages.append({'path': path, 'text': text, 'title': f'Страница {number+1}'})
        finally:
            pdfium_lock.release()
        return prepared
    except PDFImportError:
        prepared.close()
        raise
    except Exception as error:
        prepared.close()
        raise PDFImportError('Не удалось прочитать PDF. Проверьте файл; защищённый паролем или повреждённый документ не поддерживается.') from error

def save_pdf_pages(book, prepared):
    if book.pages.exists(): raise PDFImportError('В книге уже есть страницы. Импорт не заменяет существующие страницы.')
    created_files = []
    try:
        with transaction.atomic():
            for number, record in enumerate(prepared.pages, 1):
                page = BookPage(book=book, position=number, title=record['title'], text=record['text'], pdf_full_page=True)
                with record['path'].open('rb') as file:
                    page.illustration.save(f'pdf-{number}.jpg', File(file), save=False)
                created_files.append((page.illustration.storage, page.illustration.name))
                page.save()
        return len(prepared.pages)
    except Exception:
        for storage, name in created_files: storage.delete(name)
        raise
