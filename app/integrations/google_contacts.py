import os
import pickle
from urllib.parse import urlparse
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from app.config.settings import settings
from app.utils.logger import logger

SCOPES = [
    # Read and write: importing a WhatsApp group creates contacts and a label
    'https://www.googleapis.com/auth/contacts',
    # Lets the Settings page show which account is signed in
    'openid',
    'https://www.googleapis.com/auth/userinfo.email'
]
TOKEN_PICKLE_FILE = os.path.abspath(os.path.join(settings.BASE_PATH, 'data/token.pickle'))
CLIENT_SECRETS_FILE = os.path.abspath(os.path.join(settings.BASE_PATH, 'client_secrets.json'))

def get_google_credentials():
    creds = None
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            creds = pickle.load(token)
        # A token from before a scope was added can't be refreshed into it, so sign in again
        if creds and not creds.has_scopes(SCOPES):
            logger.info("Cached Google token is missing required permissions. Signing in again...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Google access token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Could not refresh Google token ({e}). Re-authorizing...")
                creds = None
        if not creds:
            logger.info("Authorizing with Google for the first time...")
            if not os.path.exists(CLIENT_SECRETS_FILE):
                logger.error(f"'{CLIENT_SECRETS_FILE}' not found. Please download it from Google Cloud Console.")
                raise FileNotFoundError(f"'{CLIENT_SECRETS_FILE}' not found.")

            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES)
            # A Web-type OAuth client only accepts its registered redirect URI (Google matches it exactly,
            # trailing slash included), so prefer the URI registered in client_secrets.json over the setting
            registered = flow.client_config.get('redirect_uris') or []
            redirect_uri = next((u for u in registered if urlparse(u).hostname in ('localhost', '127.0.0.1')),
                                settings.GOOGLE_REDIRECT_URI)
            redirect = urlparse(redirect_uri)
            creds = flow.run_local_server(
                host=redirect.hostname or 'localhost',
                port=redirect.port or 8080,
                redirect_uri_trailing_slash=redirect_uri.endswith('/'))
        
        # Ensure parent folder exists
        os.makedirs(os.path.dirname(TOKEN_PICKLE_FILE), exist_ok=True)
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(creds, token)
    return creds

def load_cached_credentials():
    """Return the saved credentials without ever opening the browser sign-in, or None."""
    if not os.path.exists(TOKEN_PICKLE_FILE):
        return None
    with open(TOKEN_PICKLE_FILE, 'rb') as token:
        creds = pickle.load(token)
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            return None
    return creds if creds and creds.valid else None

def get_account_email(creds):
    """Return the signed-in account's email, or None if the token lacks the email scope."""
    if not creds.has_scopes(['https://www.googleapis.com/auth/userinfo.email']):
        return None
    service = build('oauth2', 'v2', credentials=creds)
    return service.userinfo().get().execute().get('email')

def sign_out():
    """Forget the cached Google token so the next sync asks the user to sign in."""
    if os.path.exists(TOKEN_PICKLE_FILE):
        os.remove(TOKEN_PICKLE_FILE)
        logger.info("Removed cached Google token.")

def client_secrets_present():
    return os.path.exists(CLIENT_SECRETS_FILE)

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

def get_label_member_count(service, label_resource_name):
    """Fetch a single label's current member count (maxMembers=0 skips the member list)."""
    group = service.contactGroups().get(resourceName=label_resource_name, maxMembers=0).execute()
    # memberCount is omitted by the API when the label is empty
    return group.get('memberCount', 0)

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
                name_entry = person.get('names', [{}])[0]
                name = name_entry.get('displayName')
                # The greeting uses only the first name; fall back to the first word of the full name
                first_name = name_entry.get('givenName') or (name.split()[0] if name else None)
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
                        "first_name": first_name,
                        "phone": primary_phone
                    })
        
        page_token = results.get('nextPageToken')
        if not page_token:
            break
            
    logger.info(f"Retrieved {len(contacts_list)} contacts from label {label_resource_name}.")
    return contacts_list

# People API limits for one request
BATCH_CREATE_MAX = 200
LABEL_MODIFY_MAX = 1000
# Retries with backoff on rate limits (429) and server errors
API_RETRIES = 5

def list_all_contacts(service):
    """Every contact's resource name, display name and phone numbers (for matching before an import)."""
    people = []
    page_token = None
    while True:
        results = service.people().connections().list(
            resourceName='people/me',
            pageSize=1000,
            personFields='names,phoneNumbers',
            pageToken=page_token
        ).execute(num_retries=API_RETRIES)
        for person in results.get('connections', []):
            names = person.get('names', [])
            people.append({
                "resourceName": person['resourceName'],
                "name": names[0].get('displayName') if names else None,
                "phones": [p.get('value') for p in person.get('phoneNumbers', []) if p.get('value')],
            })
        page_token = results.get('nextPageToken')
        if not page_token:
            break
    logger.info(f"Read {len(people)} existing Google contacts.")
    return people

def find_or_create_label(service, name):
    """Return the resource name of the user label called `name`, creating it if there is none."""
    for label in list_contact_labels(service):
        if label.get('groupType') == 'USER_CONTACT_GROUP' and \
                name.casefold() in (label.get('name', '').casefold(), label.get('formattedName', '').casefold()):
            logger.info(f"Using the existing label '{name}'.")
            return label['resourceName']
    created = service.contactGroups().create(body={"contactGroup": {"name": name}}).execute(num_retries=API_RETRIES)
    logger.info(f"Created the label '{name}'.")
    return created['resourceName']

def create_contacts_in_label(service, contacts, label_resource_name, should_stop=None):
    """Create contacts ({name, phone}) already filed under the label. Returns how many were created.

    People API writes for one account must be sent one after another, so batches run in order.
    """
    should_stop = should_stop or (lambda: False)
    created = 0
    for start in range(0, len(contacts), BATCH_CREATE_MAX):
        if should_stop():
            break
        chunk = contacts[start:start + BATCH_CREATE_MAX]
        body = {
            "contacts": [{"contactPerson": {
                "names": [{"unstructuredName": c["name"]}],
                "phoneNumbers": [{"value": c["phone"], "type": "mobile"}],
                "memberships": [
                    {"contactGroupMembership": {"contactGroupResourceName": label_resource_name}},
                    # Listed too, so setting memberships can't leave the contact out of "My contacts"
                    {"contactGroupMembership": {"contactGroupResourceName": "contactGroups/myContacts"}},
                ],
            }} for c in chunk],
            "readMask": "names",
        }
        response = service.people().batchCreateContacts(body=body).execute(num_retries=API_RETRIES)
        created += len(response.get('createdPeople', []))
        logger.info(f"Created {created} of {len(contacts)} new contacts.")
    return created

def add_contacts_to_label(service, resource_names, label_resource_name, should_stop=None):
    """Add existing contacts to the label. Returns how many were added."""
    should_stop = should_stop or (lambda: False)
    added = 0
    for start in range(0, len(resource_names), LABEL_MODIFY_MAX):
        if should_stop():
            break
        chunk = resource_names[start:start + LABEL_MODIFY_MAX]
        response = service.contactGroups().members().modify(
            resourceName=label_resource_name, body={"resourceNamesToAdd": chunk}
        ).execute(num_retries=API_RETRIES)
        # The API lists contacts it could not add (for example, one deleted since the preview)
        missing = response.get('notFoundResourceNames', [])
        if missing:
            logger.warning(f"{len(missing)} existing contacts were not found and were not added to the label.")
        added += len(chunk) - len(missing)
    return added
