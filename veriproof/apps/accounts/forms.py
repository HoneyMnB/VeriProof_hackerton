"""계정 생성 입력을 검증하는 폼."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class EmailSignUpForm(UserCreationForm):
    """이메일을 Django의 고유 username으로 사용해 중복 가입을 DB에서 막는다."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email",)

    email = forms.EmailField(label="Email")

    def clean_email(self) -> str:
        """정규화 이메일을 username으로도 쓰므로 대소문자 중복을 허용하지 않는다."""
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(username__iexact=email).exists():
            raise ValidationError("An account already uses this email.")
        return email

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class EmailAuthenticationForm(AuthenticationForm):
    """기본 Django 인증을 유지하면서 입력 이름을 이메일로 바꾼다."""

    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    def clean_username(self) -> str:
        return self.cleaned_data["username"].strip().lower()
