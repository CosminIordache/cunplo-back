from html import escape
from urllib.parse import urljoin

import logfire
import resend

from src.domain.organization import Invitation

# Cada correo en el idioma de quien invita: el invitado puede no tener cuenta todavía.
# ponytail: plantillas en código, dos idiomas; pasar a plantillas de Resend si crecen
INVITATION = {
  "es": {
    "subject": "{inviter} te invita a {organization} en Cunplo",
    "title": "Únete a <strong>{organization}</strong> en Cunplo",
    "body": "{inviter} te ha invitado a su equipo en Cunplo, el asistente que convierte "
    "tus correos en tareas. Pulsa el botón y regístrate con tu cuenta de Google de "
    "{email}: entrarás directamente en {organization}.",
    "button": "Unirme a {organization}",
    "note": "Si ya usas Cunplo con este correo, entra y acepta la invitación desde "
    "Ajustes → Organización. Caduca en 7 días.",
  },
  "en": {
    "subject": "{inviter} invited you to {organization} on Cunplo",
    "title": "Join <strong>{organization}</strong> on Cunplo",
    "body": "{inviter} invited you to their team on Cunplo, the assistant that turns "
    "your emails into tasks. Click the button and sign up with the Google account for "
    "{email}: you'll go straight into {organization}.",
    "button": "Join {organization}",
    "note": "If you already use Cunplo with this email, sign in and accept the invitation "
    "from Settings → Organization. It expires in 7 days.",
  },
}


# ponytail: SVG de la landing; Gmail y Outlook no pintan SVG (sale el alt). Un PNG si hace falta
LOGO = "https://www.cunplo.com/cunplo-logo-animated-dark.svg"


class ResendService:
  """Correos transaccionales vía Resend. Sin api key no envía nada (desarrollo)."""

  def __init__(self, api_key: str, sender: str, frontend_url: str):
    self.enabled = bool(api_key)
    self.sender = sender
    self.frontend_url = frontend_url
    resend.api_key = api_key  # el SDK la lee de aquí: es global

  async def send_invitation(
    self, invitation: Invitation, inviter: str, language: str
  ) -> None:
    """Avisa al invitado. Nunca lanza: la invitación ya existe y se ve en la app,
    el correo solo es el aviso."""
    if not self.enabled:
      logfire.info("Invitation email skipped (no RESEND_API_KEY) for {email}", email=invitation.email)
      return
    text = INVITATION.get(language, INVITATION["en"])
    # nombre de la organización e invitador los escriben usuarios: escapados en el HTML
    values = {
      "inviter": escape(inviter),
      "organization": escape(invitation.organization_name),
      "email": escape(invitation.email),
    }
    # la página del front que la guarda y la acepta en el onboarding (o lleva a Ajustes)
    link = urljoin(self.frontend_url, f"/invitation/{invitation.id}")
    html = f"""
        <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#111">
        <p style="margin:0 0 24px">
            <img src="{LOGO}" width="28" height="28" alt="Cunplo" style="display:inline-block;vertical-align:middle;border:0">
            <span style="vertical-align:middle;margin-left:6px;font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:600;letter-spacing:-0.01em">Cunplo</span>
        </p>
        <h1 style="font-size:22px;font-weight:600">{text["title"].format(**values)}</h1>
        <p style="font-size:15px;line-height:1.5;color:#444">{text["body"].format(**values)}</p>
        <p style="margin:28px 0">
            <a href="{escape(link)}" style="background:#111;color:#fff;padding:12px 20px;border-radius:999px;text-decoration:none;font-size:14px">{text["button"].format(**values)}</a>
        </p>
        <p style="font-size:13px;color:#888">{text["note"]}</p>
        </div>
    """
    
    
    try:
      await resend.Emails.send_async({
        "from": self.sender,
        "to": [invitation.email],
        # el asunto va en texto plano: sin escapar
        "subject": text["subject"].format(inviter=inviter, organization=invitation.organization_name),
        "html": html,
      })
      logfire.info("Invitation email sent to {email}", email=invitation.email)
    except Exception as error:
      logfire.warning(
        "Invitation email to {email} failed: {error}", email=invitation.email, error=repr(error)
      )
