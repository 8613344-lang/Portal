import json
from pathlib import Path
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from library.models import Book, BookPage

class Command(BaseCommand):
    help = 'Import one book or array of books from JSON with local illustration/audio files.'
    def add_arguments(self, parser):
        parser.add_argument('manifest')
        parser.add_argument('--assets-dir', required=True)
    def handle(self, *args, **options):
        root = Path(options['assets_dir']).resolve(strict=True)
        records = json.loads(Path(options['manifest']).read_text(encoding='utf-8'))
        if isinstance(records, dict): records = [records]
        if not isinstance(records, list): raise CommandError('JSON must be a book object or array.')
        def asset(name):
            if not name: return None
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file(): raise CommandError('Asset path must stay inside assets-dir.')
            return File(path.open('rb'), name=path.name)
        for record in records:
            if not isinstance(record, dict): raise CommandError('Each book must be an object.')
            slug = record.get('slug')
            if Book.objects.filter(slug=slug).exists():
                self.stdout.write(f'Skipped existing book: {slug}')
                continue
            files = []
            try:
                with transaction.atomic():
                    book = Book(title=record.get('title', ''), slug=slug or '', description=record.get('description', ''), child_name=record.get('child_name', ''), age_label=record.get('age_label', 'Для чтения вместе'), status=record.get('status', 'draft'), visibility=record.get('visibility', 'restricted'))
                    book.pdf_layout = record.get('pdf_layout', 'combined')
                    book.background_theme = record.get('background_theme', 'paper')
                    background = asset(record.get('background_image'))
                    if background: book.background_image = background; files.append(background)
                    cover = asset(record.get('cover'))
                    if cover: book.cover = cover; files.append(cover)
                    book.full_clean(); book.save()
                    pages = record.get('pages', [])
                    if not isinstance(pages, list) or not 1 <= len(pages) <= 500:
                        raise CommandError('Each book needs 1–500 pages.')
                    for position, data in enumerate(pages, 1):
                        page = BookPage(book=book, position=position, title=data.get('title', ''), text=data.get('text', ''))
                        for field in ['illustration', 'audio']:
                            file = asset(data.get(field))
                            if file: setattr(page, field, file); files.append(file)
                        page.full_clean(); page.save()
                self.stdout.write(f'Imported: {book.slug} ({len(pages)} pages)')
            except Exception as exc:
                raise CommandError(f'Import failed for {slug}: {exc}') from exc
            finally:
                for file in files: file.close()
