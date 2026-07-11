import smtplib
from email.message import EmailMessage

from ..core.config import settings


class EmailNotificationConfigError(RuntimeError):
    pass


def _recipients() -> list[str]:
    return [item.strip() for item in settings.WEEKLY_VIDEO_NOTIFY_TO.split(",") if item.strip()]


def is_email_notification_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.WEEKLY_VIDEO_NOTIFY_FROM and _recipients())


def send_email_notification(subject: str, body: str) -> None:
    recipients = _recipients()
    if not settings.SMTP_HOST:
        raise EmailNotificationConfigError("SMTP_HOST is not configured")
    if not settings.WEEKLY_VIDEO_NOTIFY_FROM:
        raise EmailNotificationConfigError("WEEKLY_VIDEO_NOTIFY_FROM is not configured")
    if not recipients:
        raise EmailNotificationConfigError("WEEKLY_VIDEO_NOTIFY_TO is not configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.WEEKLY_VIDEO_NOTIFY_FROM
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if settings.SMTP_USE_SSL else smtplib.SMTP
    with smtp_cls(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
        if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(message)
