import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from app.config.settings import settings
from app.utils.logger import logger

SCOPES = ['https://www.googleapis.com/auth/contacts.readonly']
TOKEN_PICKLE_FILE = os.path.abspath(os.path.join(settings.BASE_PATH, 'data/token.pickle'))
CLIENT_SECRETS_FILE = os.path.abspath(os.path.join(settings.BASE_PATH, 'client_secrets.json'))

def get_google_credentials():
    creds = None
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Google access token...")
            creds.refresh(Request())
        else:
            logger.info("Authorizing with Google for the first time...")
            if not os.path.exists(CLIENT_SECRETS_FILE):
                logger.error(f"'{CLIENT_SECRETS_FILE}' not found. Please download it from Google Cloud Console.")
                raise FileNotFoundError(f"'{CLIENT_SECRETS_FILE}' not found.")

            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Ensure parent folder exists
        os.makedirs(os.path.dirname(TOKEN_PICKLE_FILE), exist_ok=True)
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(creds, token)
    return creds

def get_people_service():
    creds = get_google_credentials()
    service = build('people', 'v1', credentials=creds)
    return service

def list_contact_labels(service):
    logger.info("Listing Google Contact Labels...")
    labels = []
    page_token = None
    while True:
        results = service.contactGroups().list(
            pageSize=1000,
            pageToken=page_token
        ).execute()
        labels.extend(results.get('contactGroups', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    logger.debug(f"Found {len(labels)} labels.")
    return labels

def get_contacts_from_label(service, label_resource_name):
    logger.info(f"Fetching contacts from label: {label_resource_name}")
    contacts_list = []
    page_token = None
    while True:
        results = service.people().connections().list(
            resourceName='people/me',
            pageSize=1000,
            personFields='names,phoneNumbers,emailAddresses,memberships',
            pageToken=page_token
        ).execute()
        connections = results.get('connections', [])

        for person in connections:
            memberships = person.get('memberships', [])
            is_member_of_label = False
            for membership in memberships:
                if 'contactGroupMembership' in membership and \
                   membership['contactGroupMembership'].get('contactGroupResourceName') == label_resource_name:
                    is_member_of_label = True
                    break

            if is_member_of_label:
                name = person.get('names', [{}])[0].get('displayName')
                phone_numbers = person.get('phoneNumbers', [])
                primary_phone = None
                for phone in phone_numbers:
                    if phone.get('type') == 'mobile':
                        primary_phone = phone.get('value')
                        break
                if not primary_phone and phone_numbers:
                    primary_phone = phone_numbers[0].get('value')

                if name and primary_phone:
                    contacts_list.append({
                        "name": name,
                        "phone": primary_phone
                    })
        
        page_token = results.get('nextPageToken')
        if not page_token:
            break
            
    logger.info(f"Retrieved {len(contacts_list)} contacts from label {label_resource_name}.")
    return contacts_list
