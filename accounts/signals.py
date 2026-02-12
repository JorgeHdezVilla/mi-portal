from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import Owner
from accounts.models import OwnerAccount


@receiver(post_save, sender=Owner)
def create_owner_user_and_send_set_password(sender, instance: Owner, created: bool, **kwargs):
    if not created:
        return

    email = (instance.email or "").strip().lower()
    if not email:
        return

    User = get_user_model()

    # Si ya existe un user con ese email/username, no creamos otro
    if User.objects.filter(username=email).exists():
        return

    with transaction.atomic():
        user = User.objects.create_user(
            username=email,   # ✅ login con username = email
            email=email,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        OwnerAccount.objects.create(user=user, owner=instance)

    # Token + uid para link seguro
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    path = reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})

    # Dominio base (configurable)
    domain = getattr(settings, "APP_DOMAIN", "localhost:8000")
    protocol = "https" if getattr(settings, "APP_USE_HTTPS", False) else "http"
    link = f"{protocol}://{domain}{path}"

    subject = "Define tu contraseña - Acceso a la app"
    message = (
        f"Hola {instance.first_name},\n\n"
        f"Para activar tu acceso, define tu contraseña en este enlace:\n\n"
        f"{link}\n\n"
        f"Si no solicitaste esto, ignora el correo.\n"
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@miportal.local"),
        recipient_list=[email],
        fail_silently=False,
    )
