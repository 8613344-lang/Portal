import uuid
from django.db import migrations, models


def populate_tokens(apps, schema_editor):
    Book = apps.get_model('library', 'Book')
    for book in Book.objects.using(schema_editor.connection.alias).filter(share_token__isnull=True).iterator():
        book.share_token = uuid.uuid4()
        book.save(update_fields=['share_token'], using=schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [('library', '0004_book_ending_panel_color_and_more')]
    operations = [
        migrations.AddField(model_name='book', name='share_token', field=models.UUIDField(null=True, unique=True, editable=False)),
        migrations.RunPython(populate_tokens, migrations.RunPython.noop),
        migrations.AlterField(model_name='book', name='share_token', field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
    ]
