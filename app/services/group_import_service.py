"""Decide what an import from a WhatsApp group does with each member, before anything is written."""
from app.services.contact_service import normalize_phone_number

ACTION_CREATE = "create"   # New Google contact, filed under the label
ACTION_ADD = "add"         # Already a Google contact: just add it to the label
ACTION_SKIP = "skip"       # Can't be imported (see the row's detail)

def _index_contacts(existing_contacts):
    by_phone, by_name = {}, {}
    for contact in existing_contacts:
        for phone in contact["phones"]:
            # Existing contacts can hold anything in a phone field, so don't log each bad one
            normalized = normalize_phone_number(phone, log_failures=False)
            if normalized:
                by_phone.setdefault(normalized, contact)
        if contact["name"]:
            by_name.setdefault(contact["name"].casefold(), []).append(contact)
    return by_phone, by_name

def plan_import(members, existing_contacts):
    """Return one row per member: {name, phone, action, detail, resourceName}.

    Members whose number WhatsApp shows are matched to Google contacts by number, and new
    contacts are created for the rest. Members shown by name only (saved in your phone, or
    with a hidden number) are matched by exact name, and skipped when that is not certain.
    """
    by_phone, by_name = _index_contacts(existing_contacts)
    rows, seen = [], set()

    for member in members:
        name, raw_phone = member.get("name"), member.get("phone")
        row = {"name": name or raw_phone or "(no name)", "phone": raw_phone or "", "action": ACTION_SKIP,
               "detail": "", "resourceName": None}

        if raw_phone:
            phone = normalize_phone_number(raw_phone)
            if not phone:
                row["detail"] = "Not a valid phone number"
            elif phone in seen:
                continue # Listed twice (the list can repeat a row while it scrolls)
            else:
                seen.add(phone)
                row["phone"] = phone
                match = by_phone.get(phone)
                if match:
                    if match["resourceName"] in seen:
                        continue
                    seen.add(match["resourceName"])
                    row.update(action=ACTION_ADD, resourceName=match["resourceName"],
                               name=match["name"] or row["name"], detail="Already in your contacts")
                else:
                    row["action"] = ACTION_CREATE
                    if not name:
                        row["name"] = phone
                        row["detail"] = "New contact (WhatsApp showed no name)"
                    else:
                        row["detail"] = "New contact"
        elif name:
            matches = by_name.get(name.casefold(), [])
            if len(matches) == 1:
                match = matches[0]
                if match["resourceName"] in seen:
                    continue
                seen.add(match["resourceName"])
                phone = next((p for p in (normalize_phone_number(p, log_failures=False) for p in match["phones"]) if p), "")
                row.update(action=ACTION_ADD, resourceName=match["resourceName"], phone=phone,
                           detail="Matched to your contact by name")
            elif matches:
                row["detail"] = f"Number hidden, and {len(matches)} of your contacts have this name"
            else:
                row["detail"] = "Number hidden by WhatsApp, and no contact has this name"
        else:
            continue # A row with neither name nor number isn't a member

        rows.append(row)
    return rows

def summarize(rows):
    return {action: sum(r["action"] == action for r in rows) for action in (ACTION_CREATE, ACTION_ADD, ACTION_SKIP)}
