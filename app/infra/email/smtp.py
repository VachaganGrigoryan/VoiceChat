from __future__ import annotations

from html import escape
from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings
from app.core.errors import AppError
from app.infra.email.base import EmailSender


class SmtpEmailSender(EmailSender):
    async def send_verification_code(self, *, to_email: str, code: str) -> None:
        subject = "Your verification code"

        msg = self._build_verification_email(
            to_email=to_email,
            subject=subject,
            code=code,
        )

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_pass or None,
                timeout=10,
                **self._smtp_security_options(),
            )
        except Exception as e:
            raise AppError(
                code="EMAIL_SEND_FAILED",
                message="Failed to send verification email",
                status_code=502,
                details=str(e),
            ) from e

    def _build_verification_email(
        self,
        *,
        to_email: str,
        subject: str,
        code: str,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self._format_from_header()
        msg["To"] = to_email
        msg["Subject"] = subject

        plain_text = self._build_verification_text(code=code)
        html_content = self._build_verification_html(code=code)

        msg.set_content(plain_text)
        msg.add_alternative(html_content, subtype="html")

        return msg

    def _format_from_header(self) -> str:
        sender_name = settings.smtp_from_name
        sender_email = settings.smtp_from_email

        if sender_name:
            return f"{sender_name} <{sender_email}>"
        return sender_email

    def _smtp_security_options(self) -> dict[str, bool]:
        if settings.smtp_port == 587:
            return {"start_tls": True}
        if settings.smtp_port == 465:
            return {"use_tls": True}
        return {}

    def _build_verification_text(self, *, code: str) -> str:
        return (
            "Your verification code\n\n"
            f"Code: {code}\n\n"
            "This code expires soon. If you did not request it, you can ignore this email."
        )

    def _build_verification_html(self, *, code: str) -> str:
        safe_code = escape(code)
        app_name = escape(settings.web_app_name)
        app_url = escape(settings.web_app_url)

        return f"""\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{app_name} verification code</title>
  </head>
  <body style="margin:0;padding:0;background-color:#f4f7fb;font-family:Arial,Helvetica,sans-serif;color:#1f2937;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color:#f4f7fb;margin:0;padding:24px 0;">
      <tr>
        <td align="center">

          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:28px 32px;background:#111827;">
                <div style="font-size:20px;font-weight:700;color:#ffffff;">{app_name}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:32px;">
                <h2 style="margin:0 0 12px;font-size:24px;color:#111827;">Confirm your email</h2>
                <p style="margin:0 0 24px;font-size:15px;line-height:24px;color:#4b5563;">
                  Enter this verification code in the app to continue:
                </p>

                <div style="text-align:center;margin:24px 0;">
                  <span style="display:inline-block;padding:16px 28px;border-radius:14px;background:#eff6ff;border:1px solid #bfdbfe;font-size:30px;font-weight:700;letter-spacing:8px;color:#1d4ed8;">
                    {safe_code}
                  </span>
                </div>

                <p style="margin:24px 0 0;font-size:14px;line-height:22px;color:#6b7280;">
                  This code expires soon. If you didn’t request this, you can ignore this email.
                </p>
              </td>
            </tr>
            <tr>
              <td style="padding:20px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;">
                <p style="margin:0;font-size:12px;line-height:20px;color:#9ca3af;">
                  Automated email from {app_name} - {app_url}
                </p>
              </td>
            </tr>
          </table>

          <p style="margin:16px 0 0;font-size:12px;line-height:18px;color:#9ca3af;">
            © Your App. All rights reserved.
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""