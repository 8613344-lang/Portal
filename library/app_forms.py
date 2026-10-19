from django import forms


class DeleteAccountForm(forms.Form):
    password = forms.CharField(
        label='Текущий пароль', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}),
    )
    confirm = forms.BooleanField(label='Подтверждаю, что хочу безвозвратно удалить свою учётную запись')

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data['password']
        if self.user is None or not self.user.check_password(password):
            raise forms.ValidationError('Неверный пароль. Учётная запись не удалена.')
        return password
