from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm
from django import forms


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        value = (username or kwargs.get('email') or '').strip().lower()
        users = User.objects.filter(email__iexact=value) if value else User.objects.none()
        user = users.first() if users.count() == 1 else None
        if request and request.path.startswith('/admin/') and user is None:
            user = User.objects.filter(username=username, is_staff=True).first()
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        if user is None:
            User().set_password(password)


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label='Email', max_length=254, widget=forms.EmailInput(attrs={'autocomplete':'email'}))
    error_messages = {'invalid_login':'Неверный email или пароль. Проверьте также подтверждение почты.', 'inactive':'Аккаунт неактивен.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].max_length = 254
        self.fields['username'].widget.attrs['maxlength'] = 254
