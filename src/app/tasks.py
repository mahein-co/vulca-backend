from celery import shared_task
from django.core.mail import send_mail

@shared_task
def send_registration_email(subject, message, sender, recipient):
    send_mail(subject, message, sender, [recipient], fail_silently=False)

