from django import forms
from django.contrib.auth.forms import AuthenticationForm
from apps.accounts.models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'input input-bordered w-full',
            'placeholder': 'Username',
            'autofocus': True,
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'input input-bordered w-full',
            'placeholder': 'Password',
        })
    )


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'input input-bordered w-full'}),
        label='Password'
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'contact_number', 'role', 'password']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'role': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'contact_number', 'role', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'role': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }
