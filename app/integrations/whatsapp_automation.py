import time
import os
from selenium import webdriver
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from app.config.settings import settings
from app.utils.logger import logger

SEND_BUTTON_SELECTORS = [
    'span[data-testid="send"]',
    'div[aria-label="Send"]',
    'button[aria-label="Send"]',
    'span[data-icon="send"]',
    'span[data-icon="wds-ic-send-filled"]'
]

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}

SEARCH_BOX_SELECTORS = [
    'div[contenteditable="true"][data-tab="3"]',
    'div[aria-label="Search input textbox"][contenteditable="true"]',
    'div[aria-label="Search"][contenteditable="true"]',
    'input[aria-label="Search or start a new chat"]',
    'input[role="textbox"][aria-label*="Search"]',
    '#side div[contenteditable="true"]'
]

# WhatsApp Web modal dialogs (announcements, "open in another window", etc.) that block clicks
POPUP_SELECTORS = [
    'div[data-testid="confirm-popup"]',
    'div[data-animate-modal-popup="true"]'
]

# Popup buttons that are safe to press to get a dialog out of the way
SAFE_POPUP_BUTTONS = ["ok", "got it", "close", "not now", "continue", "dismiss", "skip", "use here", "later"]

def _xpath_literal(value: str) -> str:
    """Quote a string for use in XPath, including names that contain quotes."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat('" + value.replace("'", "', \"'\", '") + "')"

class WhatsAppAutomation:
    def __init__(self):
        self.driver = None
        self.wait = None
        # Counts every Send press (text or attachment), so callers know if anything left this browser
        self.sends_clicked = 0
        self.session_path = settings.RESOLVED_WHATSAPP_SESSION_PATH
        os.makedirs(self.session_path, exist_ok=True)

    def launch_browser(self):
        logger.info("Launching Chrome browser for WhatsApp Web automation...")
        options = Options()
        # Set user data directory for persistent session
        options.add_argument(f"--user-data-dir={self.session_path}")
        # options.add_argument("--headless") # Uncomment for headless mode (no UI)
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, 60) # Increased wait time for slow loading
        self.driver.get("https://web.whatsapp.com/")
        logger.info("Browser launched. Waiting for WhatsApp Web to load.")

    def _wait_for_any(self, css_selectors, clickable=False):
        # Joined into one CSS selector list so a stale selector doesn't cost a full timeout each
        combined = ", ".join(css_selectors)
        condition = EC.element_to_be_clickable if clickable else EC.presence_of_element_located
        return self.wait.until(condition((By.CSS_SELECTOR, combined)))

    def _insert_text(self, element, text: str):
        # ChromeDriver's send_keys rejects characters outside the BMP (most emoji), so insert
        # those lines through the browser's own text insertion instead
        if all(ord(ch) <= 0xFFFF for ch in text):
            element.send_keys(text)
        else:
            self.driver.execute_script(
                "arguments[0].focus(); document.execCommand('insertText', false, arguments[1]);",
                element, text
            )

    def _type_message(self, element, message: str):
        # A raw "\n" presses Enter, which sends each line as a separate message; use Shift+Enter
        lines = message.split("\n")
        for i, line in enumerate(lines):
            if line:
                self._insert_text(element, line)
            if i < len(lines) - 1:
                element.send_keys(Keys.SHIFT, Keys.ENTER)

    def wait_for_login(self):
        try:
            # Wait until the main chat list element or search box is visible, indicating successful login
            login_selector = '//*[@id="pane-side"] | //*[@data-testid="chat-list-search"] | //*[@data-testid="list-area-wrap"] | //*[@data-testid="chatlist-search-input"]'
            self.wait.until(EC.presence_of_element_located((By.XPATH, login_selector)))
            logger.info("WhatsApp Web loaded successfully. User is logged in.")
            return True
        except TimeoutException:
            logger.error("Timeout waiting for WhatsApp Web login. Please scan QR code if not already logged in.")
            return False

    def send_message(self, recipient_phone: str, message: str, attachment_path: str = None, caption: str = None):
        if not self.driver:
            logger.error("Browser not launched. Call launch_browser() first.")
            return False

        logger.info(f"Attempting to send message to {recipient_phone}")
        try:
            # Open chat with the recipient
            # A raw "+" in the query string decodes to a space, so pass digits only
            self.driver.get(f"https://web.whatsapp.com/send?phone={recipient_phone.lstrip('+')}")
            time.sleep(5) # Give WhatsApp Web time to load the chat
        except Exception as e:
            logger.error(f"Could not open the chat with {recipient_phone}: {e}")
            return False
        return self._send_in_open_chat(recipient_phone, message, attachment_path, caption)

    def send_to_group(self, group_name: str, message: str, attachment_path: str = None, caption: str = None):
        if not self.driver:
            logger.error("Browser not launched. Call launch_browser() first.")
            return False

        logger.info(f"Attempting to send message to group '{group_name}'")
        if not self._open_group_chat(group_name):
            return False
        return self._send_in_open_chat(f"group '{group_name}'", message, attachment_path, caption)

    def _visible_popups(self):
        found = self.driver.find_elements(By.CSS_SELECTOR, ", ".join(POPUP_SELECTORS))
        return [p for p in found if p.is_displayed()]

    def dismiss_popups(self, manual_wait_seconds: int = 60) -> bool:
        """Close WhatsApp Web dialogs that block the page. Returns False if one stays open."""
        popups = self._visible_popups()
        if not popups:
            return True

        text = " ".join(popups[0].text.split())[:200]
        logger.warning(f"WhatsApp Web is showing a popup: \"{text}\". Trying to close it...")

        # Escape closes most WhatsApp dialogs
        ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)

        # Otherwise press a button whose label is clearly harmless
        for popup in self._visible_popups():
            for button in popup.find_elements(By.CSS_SELECTOR, 'button, div[role="button"]'):
                if button.text.strip().lower() in SAFE_POPUP_BUTTONS and button.is_displayed():
                    logger.info(f"Closing popup with its '{button.text.strip()}' button.")
                    button.click()
                    time.sleep(1)
                    break

        # Anything left needs a person to decide, so wait for them to close it
        waited = 0
        while self._visible_popups() and waited < manual_wait_seconds:
            if waited == 0:
                logger.warning(f"Please close the popup in the WhatsApp window. Waiting up to {manual_wait_seconds} seconds...")
            time.sleep(2)
            waited += 2

        if self._visible_popups():
            logger.error("A WhatsApp Web popup is still blocking the page.")
            return False
        logger.info("Popup closed.")
        return True

    def _focus(self, element):
        # A leftover overlay can intercept a real click; focusing through the page still works
        try:
            element.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].focus();", element)

    def _open_group_chat(self, group_name: str) -> bool:
        """Find the group in the chat list by its exact name and open it."""
        name_literal = _xpath_literal(group_name)
        if not self.dismiss_popups():
            return False
        try:
            search_box = self._wait_for_any(SEARCH_BOX_SELECTORS, clickable=True)
            self._focus(search_box)
            # Clear any previous search before typing the group name
            search_box.send_keys(Keys.CONTROL, "a")
            search_box.send_keys(Keys.BACKSPACE)
            self._insert_text(search_box, group_name)
            time.sleep(2) # Give the chat list time to filter

            # Exact title match only, so a similarly named chat is never picked
            result = WebDriverWait(self.driver, 15).until(EC.element_to_be_clickable((
                By.XPATH, f"//div[@id='pane-side']//span[@title={name_literal}]"
            )))
            try:
                result.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].click();", result)
            time.sleep(2) # Give the chat time to open
        except TimeoutException:
            logger.error(f"Group '{group_name}' was not found in your WhatsApp chats. Check that the name matches exactly and that you are a member.")
            return False
        except Exception as e:
            logger.error(f"Could not open group '{group_name}': {e}")
            return False

        # Make sure the open conversation is the group before sending anything to it
        try:
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((
                By.XPATH,
                f"//div[@id='main']//header//*[@title={name_literal} or normalize-space(text())={name_literal}]"
            )))
        except TimeoutException:
            logger.error(f"Opened a chat but could not confirm it is group '{group_name}'. Nothing was sent to it.")
            return False

        # Clear the search so the chat list is back to normal for the next group
        try:
            search_box.send_keys(Keys.CONTROL, "a")
            search_box.send_keys(Keys.BACKSPACE)
        except Exception:
            pass
        return True

    def _send_in_open_chat(self, recipient: str, message: str, attachment_path: str = None, caption: str = None):
        """Send the text (if any) and then the attachment (if any) in the currently open chat."""
        if not self.dismiss_popups():
            return False
        try:
            # Wait for the message input box to be present
            message_box = self._wait_for_any([
                'div[data-testid="conversation-compose-box-input"]',
                'footer div[contenteditable="true"][role="textbox"]',
                'div[aria-label="Type a message"][contenteditable="true"]',
                'footer div[contenteditable="true"]'
            ])

            if message:
                self._focus(message_box)
                self._type_message(message_box, message)
                time.sleep(1) # Small delay after typing message

                # Send the text on its own first, so the compose box is empty when the attachment
                # preview opens (otherwise WhatsApp can turn the draft into the attachment caption)
                self._click_send_button()
                logger.info(f"Message sent successfully to {recipient}")
                time.sleep(2) # Delay after sending message

            if attachment_path:
                if not self._attach_file(attachment_path, caption):
                    if message:
                        logger.error(f"Text was sent to {recipient}, but the attachment was not.")
                    return False
            return True
        except TimeoutException:
            logger.error(f"Timeout while sending message to {recipient}. Chat or input field not found.")
            return False
        except NoSuchElementException as e:
            logger.error(f"Element not found for {recipient}: {e}")
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred while sending message to {recipient}: {e}")
            return False

    def _click_send_button(self):
        send_button = None
        try:
            send_button = self._wait_for_any(SEND_BUTTON_SELECTORS, clickable=True)
        except TimeoutException:
            pass

        if not send_button:
            try:
                send_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@data-icon='send'] | //div[@aria-label='Send']")))
            except TimeoutException:
                raise TimeoutException("Send button not found.")
        send_button.click()
        self.sends_clicked += 1

    def _attach_file(self, file_path: str, caption: str = None) -> bool:
        logger.info(f"Attempting to attach file: {file_path}")
        is_image = os.path.splitext(file_path)[1].lower() in IMAGE_EXTENSIONS
        try:
            # Click the attach button (trying old clip/paperclip icon and new plus icon)
            attach_selectors = [
                'span[data-icon="plus"]',
                'div[aria-label="Attach"]',
                'button[aria-label="Attach"]',
                'div[data-testid="clip"]',
                'span[data-icon="attach-menu-plus"]',
                'span[data-icon="plus-rounded"]',
                'button[title="Attach"]'
            ]
            attach_button = None
            try:
                attach_button = self._wait_for_any(attach_selectors, clickable=True)
            except TimeoutException:
                pass

            if not attach_button:
                try:
                    attach_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[@data-icon='plus'] | //div[@aria-label='Attach']")))
                except TimeoutException:
                    raise TimeoutException("Attach button not found.")

            attach_button.click()
            time.sleep(1.5) # Give time for attachment options to appear

            # Find the hidden file input. Images go through the "Photos & videos" input;
            # anything else (PDF) must use the "Document" input, which accepts any type.
            if is_image:
                input_selectors = [
                    'input[accept*="image/"]',
                    'input[type="file"][accept*="image"]',
                    'input[type="file"]'
                ]
            else:
                input_selectors = [
                    'input[type="file"][accept="*"]',
                    'input[type="file"]:not([accept*="image"])'
                ]
            upload_input = None
            for selector in input_selectors:
                found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if found:
                    upload_input = found[0]
                    break

            if not upload_input:
                xpath = "//input[@type='file']" if is_image else "//input[@type='file' and not(contains(@accept, 'image'))]"
                try:
                    upload_input = self.wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
                except TimeoutException:
                    raise TimeoutException("Upload input element not found.")

            # Send the file path to the hidden input element
            upload_input.send_keys(file_path)
            logger.info(f"File path sent to upload input: {file_path}")
            time.sleep(4) # Wait for the preview to load

            if caption:
                self._type_caption(caption)

            # Click the final send button for the attachment (with robust fallbacks)
            self._click_send_button()
            logger.info(f"Attachment '{os.path.basename(file_path)}' sent.")
            time.sleep(2) # Delay after sending attachment
            return True
        except TimeoutException:
            logger.error(f"Timeout while attaching file {file_path}. Upload elements not found.")
        except NoSuchElementException as e:
            logger.error(f"Attachment element not found for {file_path}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while attaching file {file_path}: {e}")
        return False

    def _type_caption(self, caption: str):
        caption_selectors = [
            'div[aria-label="Add a caption"][contenteditable="true"]',
            'div[aria-placeholder="Add a caption"][contenteditable="true"]',
            'div[data-testid="media-caption-input-container"] div[contenteditable="true"]'
        ]
        try:
            # Short wait: the preview is already open, so a missing box means the selectors are stale
            caption_box = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ", ".join(caption_selectors)))
            )
            caption_box.click()
            self._type_message(caption_box, caption)
            time.sleep(0.5)
        except TimeoutException:
            logger.warning("Caption box not found in the attachment preview. Sending the attachment without a caption.")

    def close_browser(self):
        if self.driver:
            logger.info("Closing browser.")
            self.driver.quit()

# Main execution for testing WhatsApp automation directly
if __name__ == '__main__':
    # This part is for direct testing and would not be part of the main app flow.
    # Ensure .env is configured and a valid phone number is provided.
    # Create a dummy flyer for testing
    dummy_flyer_path = os.path.join(settings.BASE_PATH, "data", "test_flyer.png")
    try:
        from PIL import Image
        os.makedirs(os.path.dirname(dummy_flyer_path), exist_ok=True)
        img = Image.new('RGB', (60, 30), color = 'red')
        img.save(dummy_flyer_path)
        logger.info(f"Created dummy flyer at {dummy_flyer_path}")
    except ImportError:
        logger.warning("Pillow not installed. Cannot create dummy flyer. Please install with 'pip install Pillow'.")
        dummy_flyer_path = None

    wa_automation = WhatsAppAutomation()
    wa_automation.launch_browser()
    if wa_automation.wait_for_login():
        test_phone_number = input("Enter a phone number to send a test message to (e.g., +1XXXXXXXXXX): ")
        test_message = "Hello from your automation script! Please ignore this test message."
        
        if test_phone_number:
            wa_automation.send_message(test_phone_number, test_message, attachment_path=dummy_flyer_path, caption="Test caption")
        else:
            logger.warning("No phone number provided for testing.")
    wa_automation.close_browser()

    if dummy_flyer_path and os.path.exists(dummy_flyer_path):
        os.remove(dummy_flyer_path)
        logger.info(f"Removed dummy flyer at {dummy_flyer_path}")
