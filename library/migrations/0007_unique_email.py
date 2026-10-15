from django.db import migrations


def audit(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    records = list(User.objects.using(schema_editor.connection.alias).values_list('pk','email'))
    seen = set()
    for pk, email in records:
        normalized = email.strip().lower()
        if normalized and normalized in seen:
            raise RuntimeError('Есть пользователи с одинаковым email. Исправьте email через админку перед миграцией; аккаунты автоматически не объединяются.')
        if normalized: seen.add(normalized)
    for pk, email in records:
        normalized = email.strip().lower()
        if normalized != email:
            User.objects.using(schema_editor.connection.alias).filter(pk=pk).update(email=normalized)


class Migration(migrations.Migration):
    dependencies = [('library','0006_emailconfirmation')]
    operations = [
        migrations.RunPython(audit, migrations.RunPython.noop),
        migrations.RunSQL("CREATE UNIQUE INDEX portal_user_email_unique ON auth_user (LOWER(email)) WHERE email <> ''", "DROP INDEX portal_user_email_unique"),
    ]
