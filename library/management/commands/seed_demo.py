import json
from pathlib import Path
from urllib.request import urlopen
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from library.models import Book, BookPage
from library.validators import normalize_image

class Command(BaseCommand):
    help = 'Import Dima as a public demo. Optional original images are copied to private storage.'
    def add_arguments(self, parser): parser.add_argument('--download-images', action='store_true')
    def handle(self, *args, **options):
        data_dir = Path(__file__).resolve().parents[3] / 'data'
        data = json.loads((data_dir / 'dima.json').read_text(encoding='utf-8'))
        with transaction.atomic():
            book, created = Book.objects.get_or_create(slug=data['slug'], defaults={'title': data['title'], 'description': data['description'], 'child_name': 'Дима', 'status': 'published', 'visibility': 'public'})
            if not created:
                self.stdout.write('Demo already exists; existing content and permissions preserved.')
            else:
                for number, page in enumerate(data['pages'], 1):
                    BookPage.objects.create(book=book, position=number, title=page['title'], text=page['text'])
        if created:
            self.stdout.write('Imported Dima: 12 pages. No synthetic speech; add real MP3/WAV recordings in admin.')
        for index, page_data in enumerate(data['pages']):
            page = book.pages.filter(position=index + 1).first()
            if not page or page.illustration: continue
            local = data_dir / 'demo-images' / f'{index + 1:02}.jpg'
            try:
                if local.exists():
                    content = local.read_bytes()
                elif options['download_images'] and page_data.get('image_url'):
                    with urlopen(page_data['image_url'], timeout=20) as response:
                        content = response.read(10 * 1024 * 1024 + 1)
                else: continue
                source = ContentFile(content, name='original.jpg')
                image = normalize_image(source)
                page.illustration.save('illustration.jpg', ContentFile(image.read()), save=True)
                if index == 0 and not book.cover:
                    image.seek(0)
                    book.cover.save('cover.jpg', ContentFile(image.read()), save=True)
            except Exception as exc:
                self.stderr.write(f'Illustration {index + 1} unavailable: {type(exc).__name__}. Upload it in admin.')
