from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.residents.models import Resident


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

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        if username and password:
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                user_model = get_user_model()
                pending_user = user_model._default_manager.filter(username__iexact=username).first()
                if pending_user and pending_user.check_password(password) and not pending_user.is_active:
                    raise ValidationError(
                        'Your account is pending admin approval. Please wait for activation before logging in.',
                        code='inactive',
                    )
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class ResidentSignupForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Username'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Email address'}),
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Password'}),
        label='Password',
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Confirm password'}),
        label='Confirm Password',
    )
    first_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    middle_name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    last_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    suffix = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Jr, Sr, III'}),
    )
    sex = forms.ChoiceField(
        choices=[('', '-- Select --')] + Resident.SEX_CHOICES,
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
    )
    birth_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'input input-bordered w-full', 'type': 'date'}),
    )
    contact_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    street_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'House/Unit number'}),
    )
    street_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Street name'}),
    )
    address = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Complete address'}),
    )
    valid_id_type = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full', 'placeholder': 'Government ID type'}),
    )
    valid_id_image = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'file-input file-input-bordered w-full'}),
    )

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Email address is already in use.')
        return email

    def clean_contact_number(self):
        return self.cleaned_data['contact_number'].strip()

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get('password1')
        password2 = cleaned.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Passwords do not match.')

        birth_date = cleaned.get('birth_date')
        if birth_date:
            today = timezone.localdate()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            if age < 0:
                self.add_error('birth_date', 'Birth date cannot be in the future.')
            else:
                cleaned['computed_age'] = age
        return cleaned

    @transaction.atomic
    def save(self):
        user = User(
            username=self.cleaned_data['username'],
            email=self.cleaned_data['email'],
            first_name=self.cleaned_data['first_name'],
            last_name=self.cleaned_data['last_name'],
            role=User.ROLE_RESIDENT,
            contact_number=self.cleaned_data['contact_number'],
            is_active=False,
        )
        user.set_password(self.cleaned_data['password1'])
        user.save()

        resident = Resident.objects.create(
            user=user,
            first_name=self.cleaned_data['first_name'],
            middle_name=self.cleaned_data.get('middle_name', ''),
            last_name=self.cleaned_data['last_name'],
            suffix=self.cleaned_data.get('suffix', ''),
            sex=self.cleaned_data.get('sex', ''),
            birth_date=self.cleaned_data.get('birth_date'),
            age=self.cleaned_data.get('computed_age'),
            contact_number=self.cleaned_data['contact_number'],
            address=self.cleaned_data['address'],
            street_number=self.cleaned_data.get('street_number', ''),
            street_name=self.cleaned_data.get('street_name', ''),
            valid_id_type=self.cleaned_data.get('valid_id_type', ''),
            valid_id_image=self.cleaned_data.get('valid_id_image'),
            is_approved=False,
            registered_by=None,
        )
        return user, resident


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'input input-bordered w-full'}),
        label='Password'
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'contact_number', 'role', 'password']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'email': forms.EmailInput(attrs={'class': 'input input-bordered w-full'}),
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'role': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Email address is already in use.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'contact_number', 'role', 'is_active']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'input input-bordered w-full'}),
            'first_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'last_name': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'contact_number': forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
            'role': forms.Select(attrs={'class': 'select select-bordered w-full'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Email address is already in use.')
        return email
