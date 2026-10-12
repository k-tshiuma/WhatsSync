import phonenumbers
from typing import List, Dict, Any
from app.config.settings import settings
from app.utils.logger import logger

def normalize_phone_number(phone_number: str, default_country: str | None = None) -> str | None:
    # Read at call time so a country changed in the GUI applies without a restart
    default_country = default_country or settings.DEFAULT_COUNTRY
    try:
        parsed_number = phonenumbers.parse(phone_number, default_country)
        if phonenumbers.is_valid_number(parsed_number):
            # Format to E.164 which is recommended for WhatsApp
            return phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)
        else:
            logger.warning(f"Invalid phone number after parsing: {phone_number}")
            return None
    except phonenumbers.phonenumberutil.NumberParseException as e:
        logger.warning(f"Could not parse phone number '{phone_number}': {e}")
        return None

def analyze_contacts(contacts: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """Split contacts into unique valid ones, duplicates and invalid numbers, so the GUI can show what cleanup removed."""
    valid, duplicates, invalid = [], [], []
    seen_numbers = set()

    for contact in contacts:
        name = contact.get("name")
        phone = contact.get("phone")

        if not name or not phone:
            logger.warning(f"Skipping contact due to missing name or phone: {contact}")
            invalid.append({"name": name or "(no name)", "phone": phone or ""})
            continue

        normalized_phone = normalize_phone_number(phone)

        if normalized_phone:
            if normalized_phone not in seen_numbers:
                valid.append({
                    "name": name,
                    "phone": normalized_phone
                })
                seen_numbers.add(normalized_phone)
            else:
                logger.info(f"Skipping duplicate phone number for {name}: {normalized_phone}")
                duplicates.append({"name": name, "phone": normalized_phone})
        else:
            logger.warning(f"Skipping contact {name} due to invalid phone number: {phone}")
            invalid.append({"name": name, "phone": phone})

    return {"valid": valid, "duplicates": duplicates, "invalid": invalid}

def validate_and_deduplicate_contacts(contacts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    logger.info("Validating and deduplicating contacts...")
    processed_contacts = analyze_contacts(contacts)["valid"]
    logger.info(f"Finished processing. {len(processed_contacts)} unique and valid contacts found.")
    return processed_contacts
