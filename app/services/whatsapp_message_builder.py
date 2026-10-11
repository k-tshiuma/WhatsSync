from jinja2 import Environment, FileSystemLoader
from app.config.settings import settings
from app.utils.logger import logger
import os

env = Environment(loader=FileSystemLoader(os.path.join(settings.APP_PATH, 'templates')))

def render_caption(caption: str, student_name: str) -> str:
    """Fill {{name}} / {{group_link}} placeholders in a user-entered attachment caption."""
    try:
        return env.from_string(caption).render(
            name=student_name,
            group_link=settings.WHATSAPP_GROUP_INVITE_LINK
        )
    except Exception as e:
        logger.warning(f"Could not render caption placeholders, sending it as typed: {e}")
        return caption

def build_registration_message(student_name: str) -> str:
    try:
        template = env.get_template("registration_message.txt")
        message = template.render(
            name=student_name,
            group_link=settings.WHATSAPP_GROUP_INVITE_LINK
        )
        return message
    except Exception as e:
        logger.error(f"Error building registration message for {student_name}: {e}")
        return (
            f"Hello {student_name},\n\n"
            "Registration for the new semester has started.\n\n"
            "Please see the attached flyer for details.\n\n"
            f"Join the WhatsApp group here:\n{settings.WHATSAPP_GROUP_INVITE_LINK}\n\n"
            "Thank you."
        )