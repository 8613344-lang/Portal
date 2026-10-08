from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from library.models import Book
from library.pdf_import import prepare_pdf, save_pdf_pages, PDFImportError

class Command(BaseCommand):
    help = 'Create a private draft book from PDF, preserving every sheet.'
    def add_arguments(self, parser):
        parser.add_argument('pdf')
        parser.add_argument('--title', required=True)
        parser.add_argument('--slug', required=True)
        parser.add_argument('--layout', choices=['combined', 'alternating'], default='alternating')
        parser.add_argument('--background', choices=['paper', 'white', 'mint', 'sky', 'rose'], default='paper')
    def handle(self, *args, **options):
        prepared = None
        try:
            with Path(options['pdf']).open('rb') as file: prepared = prepare_pdf(file)
            with transaction.atomic():
                book = Book(title=options['title'], slug=options['slug'], pdf_layout=options['layout'], background_theme=options['background'])
                book.full_clean(); book.save()
                count = save_pdf_pages(book, prepared)
            self.stdout.write(f'Imported {count} pages: {book.slug} (private draft)')
        except Exception as error:
            raise CommandError(str(error)) from error
        finally:
            if prepared: prepared.close()
