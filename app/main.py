import time
import os
from typing import List, Dict, Any
from app.config.settings import settings
from app.utils.logger import logger
from app.integrations.google_contacts import get_people_service, list_contact_labels, get_contacts_from_label
from app.services.contact_service import validate_and_deduplicate_contacts
from app.services.whatsapp_message_builder import build_registration_message
from app.integrations.whatsapp_automation import WhatsAppAutomation

def main():
    logger.info("Starting WhatsApp Student Registration Automation App...")

    # 1. Google Contacts Integration
    try:
        people_service = get_people_service()
        labels = list_contact_labels(people_service)

        if not labels:
            logger.error("No contact labels found in your Google Contacts. Exiting.")
            return

        print("\nAvailable Google Contact Labels:")
        for i, label in enumerate(labels):
            print(f"{i+1}. {label.get('formattedName', label.get('name', 'Unnamed Label'))}")

        selected_label_index = -1
        while not (0 < selected_label_index <= len(labels)):
            try:
                selected_label_index = int(input("Enter the number of the label to import contacts from: "))
            except ValueError:
                print("Invalid input. Please enter a number.")

        selected_label = labels[selected_label_index - 1]
        selected_label_resource_name = selected_label.get('resourceName')
        selected_label_display_name = selected_label.get('formattedName', selected_label.get('name', 'Unnamed Label'))

        print(f"Fetching contacts from: {selected_label_display_name}...")
        raw_contacts = get_contacts_from_label(people_service, selected_label_resource_name)
        if not raw_contacts:
            logger.warning(f"No contacts found in the label '{selected_label_display_name}'. Exiting.")
            return

    except Exception as e:
        logger.critical(f"Error during Google Contacts integration: {e}")
        return

    # 2. Phone Number Validation and Deduplication
    processed_contacts = validate_and_deduplicate_contacts(raw_contacts)
    if not processed_contacts:
        logger.warning("No valid or unique contacts found after processing. Exiting.")
        return
    
    print(f"Successfully processed {len(processed_contacts)} unique and valid contacts.")
    # Optional: Display a few processed contacts for review
    # print("Sample processed contacts:")
    # for contact in processed_contacts[:5]:
    #     print(f"  Name: {contact['name']}, Phone: {contact['phone']}")

    # 3. WhatsApp Integration (Semi-Automated Sending)
    if not settings.WHATSAPP_GROUP_INVITE_LINK:
        logger.error("WHATSAPP_GROUP_INVITE_LINK is not set in .env. Please configure it.")
        return

    print("\nPreparing to send WhatsApp messages...")
    print(f"WhatsApp Group Invite Link: {settings.WHATSAPP_GROUP_INVITE_LINK}")
    input("Press Enter to launch browser and start WhatsApp Web automation (ensure you are logged in or ready to scan QR code)...")

    wa_automation = WhatsAppAutomation()
    try:
        wa_automation.launch_browser()
        if not wa_automation.wait_for_login():
            print("Please scan the QR code in the browser to log in to WhatsApp Web.")
            input("Press Enter after logging in...")
            if not wa_automation.wait_for_login(): # Check again after user presses enter
                logger.error("User did not log in to WhatsApp Web. Exiting automation.")
                return

        flyer_to_attach = os.path.abspath(settings.FLYER_PATH)
        if not os.path.exists(flyer_to_attach):
            logger.warning(f"Flyer file not found at '{flyer_to_attach}'. Messages will be sent without an attachment.")
            flyer_to_attach = None

        print(f"\nStarting to send messages to {len(processed_contacts)} contacts...")
        for i, contact in enumerate(processed_contacts):
            student_name = contact['name']
            student_phone = contact['phone']
            message = build_registration_message(student_name)

            logger.info(f"[{i+1}/{len(processed_contacts)}] Sending message to {student_name} ({student_phone})")
            success = wa_automation.send_message(student_phone, message, flyer_path=flyer_to_attach)
            if not success:
                logger.error(f"Failed to send message to {student_name} ({student_phone}). Will attempt next contact.")
            
            # Implement a delay to prevent anti-spam triggers
            delay_seconds = 5 # You might want to make this configurable or dynamic
            logger.info(f"Waiting for {delay_seconds} seconds before next message...")
            time.sleep(delay_seconds)

        logger.info("All messages attempted to be sent.")

    except Exception as e:
        logger.critical(f"Error during WhatsApp automation: {e}")
    finally:
        wa_automation.close_browser()
    
    logger.info("WhatsApp Student Registration Automation App finished.")

if __name__ == "__main__":
    main()
