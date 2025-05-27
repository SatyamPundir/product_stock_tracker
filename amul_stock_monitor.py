import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import logging
import os
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from email.header import Header
from email.utils import formataddr

class StockMonitor:
    def __init__(self, config_file='config.json'):
        """Initialize the stock monitor with configuration"""
        self.config = self.load_config(config_file)
        self.setup_logging()
        self.driver = None
    
    def load_config(self, config_file):
        """Load configuration from JSON file or environment variables"""
        try:
            with open(config_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # For cloud deployment, use environment variables
            return {
                "email": {
                    "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
                    "smtp_port": int(os.getenv("SMTP_PORT", "587")),
                    "sender_email": os.getenv("SENDER_EMAIL"),
                    "sender_password": os.getenv("SENDER_PASSWORD"),
                    "recipient_email": os.getenv("RECIPIENT_EMAIL")
                },
                "products": json.loads(os.getenv("PRODUCTS_JSON", '[]')),
                "check_interval": int(os.getenv("CHECK_INTERVAL", "300")),
                "user_agent": os.getenv("USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            }
    
    def setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler()  # Only use console logging for cloud
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def setup_selenium_driver(self):
        """Setup Selenium WebDriver for cloud environment"""
        if self.driver:
            return self.driver
            
        try:
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')
            chrome_options.add_argument('--disable-images')
            chrome_options.add_argument('--disable-javascript')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument(f'--user-agent={self.config["user_agent"]}')
            
            # Cloud-specific options
            chrome_options.add_argument('--remote-debugging-port=9222')
            chrome_options.add_argument('--single-process')
            chrome_options.add_argument('--disable-web-security')
            chrome_options.add_argument('--allow-running-insecure-content')
            
            # Use system chrome in cloud environments
            chrome_binary = os.getenv("CHROME_BIN", "/usr/bin/chromium-browser")
            if os.path.exists(chrome_binary):
                chrome_options.binary_location = chrome_binary
            
            self.driver = webdriver.Chrome(options=chrome_options)
            self.logger.info("Selenium driver setup successful")
            return self.driver
            
        except Exception as e:
            self.logger.error(f"Failed to setup Selenium driver: {str(e)}")
            return None
    
    def handle_pincode_modal(self, product):
        """Handle pincode/location modal if present and active"""
        try:
            pincode = product.get('pincode')
            selectors = product.get('pincode_selectors', {})

            if not pincode:
                return True

            modal_selector = selectors.get('modal', '#locationWidgetModal')
            input_selector = selectors.get('input', '#search')

            try:
                # Wait for modal to become visible
                WebDriverWait(self.driver, 5).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, modal_selector))
                )

                # Check if input field inside modal is interactable
                WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, input_selector))
                )

                self.logger.info("Pincode modal detected and active")

            except TimeoutException:
                self.logger.info("No active pincode modal found")
                return True

            # Fill the pincode
            pincode_input = self.driver.find_element(By.CSS_SELECTOR, input_selector)
            pincode_input.clear()
            pincode_input.send_keys(pincode)
            self.logger.info(f"Entered pincode: {pincode}")

            # Select matching pincode from dropdown
            try:
                dropdown_xpath = f"//p[contains(@class, 'item-name') and text()='{pincode}']"
                matching_item = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, dropdown_xpath))
                )
                matching_item.click()
                self.logger.info(f"Selected matching pincode from dropdown: {pincode}")
            except TimeoutException:
                self.logger.warning(f"Dropdown with pincode {pincode} not found. Trying to proceed without it.")

            # Submit
            submit_selector = selectors.get('submit_button', '.btn-success')
            try:
                submit_button = self.driver.find_element(By.CSS_SELECTOR, submit_selector)
                submit_button.click()
                self.logger.info("Clicked submit button")
            except NoSuchElementException:
                from selenium.webdriver.common.keys import Keys
                pincode_input.send_keys(Keys.RETURN)
                self.logger.info("Pressed Enter on pincode input")

            # Wait for modal to disappear
            WebDriverWait(self.driver, 10).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, modal_selector))
            )

            time.sleep(2)
            self.logger.info("Pincode modal handled successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to handle pincode modal: {str(e)}")
            return False
    
    def check_stock_status(self, product):
        """Check if a product is in stock"""
        use_selenium = product.get('use_selenium', False)
        
        if use_selenium:
            return self.check_stock_with_selenium(product)
        else:
            return self.check_stock_with_requests(product)
    
    def check_stock_with_selenium(self, product):
        """Check stock status using Selenium for JavaScript-heavy sites"""
        try:
            if not self.driver:
                self.driver = self.setup_selenium_driver()
                if not self.driver:
                    return None, "Selenium driver setup failed"

            self.driver.get(product['url'])

            # Handle pincode modal if present
            if not self.handle_pincode_modal(product):
                return None, "Failed to handle pincode modal"

            # Wait for page to fully load
            WebDriverWait(self.driver, 15).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )

            # Look for out-of-stock alert
            try:
                alert_element = self.driver.find_element(By.CSS_SELECTOR, 'div.alert.alert-danger')
                if 'sold out' in alert_element.text.lower():
                    return False, "Explicit 'Sold Out' alert found"
            except NoSuchElementException:
                # No sold-out alert found — assume in stock
                return True, "No 'Sold Out' alert — assuming product is in stock"

            # Safety fallback
            return False, "Unable to determine stock status from alert element"

        except Exception as e:
            self.logger.error(f"Selenium check failed for {product['name']}: {str(e)}")
            return None, f"Selenium error: {str(e)}"

    def check_stock_with_requests(self, product):
        """Check stock status using requests for simple/static pages"""
        try:
            headers = {
                'User-Agent': self.config['user_agent'],
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }

            response = requests.get(product['url'], headers=headers, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Look for the sold out alert div
            alert_div = soup.select_one('div.alert.alert-danger.mt-3')
            if alert_div and 'sold out' in alert_div.text.lower():
                return False, "Explicit 'Sold Out' alert found"

            return True, "No 'Sold Out' alert — assuming product is in stock"

        except requests.RequestException as e:
            self.logger.error(f"Request failed for {product['name']}: {str(e)}")
            return None, f"Request failed: {str(e)}"
        except Exception as e:
            self.logger.error(f"Error checking {product['name']}: {str(e)}")
            return None, f"Error: {str(e)}"

    def send_notification(self, product_name, product_url, message):
        """Send email notification with UTF-8 support"""
        try:
            email_config = self.config['email']

            msg = MIMEMultipart()
            msg['From'] = formataddr((str(Header('Stock Bot', 'utf-8')), email_config['sender_email']))
            msg['To'] = email_config['recipient_email']
            msg['Subject'] = str(Header(f"STOCK ALERT: {product_name} is available!", 'utf-8'))

            body = (
                f"✅ The product '{product_name}' is now available!\n\n"
                f"🛒 Product URL: {product_url}\n"
                f"📦 Status: {message}\n"
                f"⏰ Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "Visit the URL to buy it now.\n"
            )

            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            server = smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port'])
            server.starttls()
            server.login(email_config['sender_email'], email_config['sender_password'])

            server.sendmail(
                email_config['sender_email'],
                email_config['recipient_email'],
                msg.as_string()
            )

            server.quit()
            self.logger.info(f"Notification sent for {product_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to send notification: {str(e)}")
            return False

    def send_telegram_notification(self, product_name, product_url, message):
        """Send Telegram notification if configured"""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not bot_token or not chat_id:
            return False
            
        try:
            telegram_message = (
                f"🚨 *STOCK ALERT*\n\n"
                f"✅ *{product_name}* is now available!\n\n"
                f"🛒 [Buy Now]({product_url})\n"
                f"📦 Status: {message}\n"
                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": telegram_message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            
            self.logger.info(f"Telegram notification sent for {product_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to send Telegram notification: {str(e)}")
            return False

    def run_single_check(self):
        """Run a single check cycle (useful for cron jobs)"""
        self.logger.info("Starting single stock check...")
        
        try:
            for product in self.config['products']:
                product_name = product['name']
                self.logger.info(f"Checking {product_name}...")
                
                is_in_stock, message = self.check_stock_status(product)
                
                if is_in_stock is None:
                    continue
                
                if is_in_stock:
                    self.logger.info(f"ALERT: {product_name} is IN STOCK!")
                    self.send_notification(product_name, product['url'], message)
                    self.send_telegram_notification(product_name, product['url'], message)
                else:
                    self.logger.info(f"WAITING: {product_name} is out of stock")
                
                time.sleep(2)
                
        except Exception as e:
            self.logger.error(f"Unexpected error during check: {str(e)}")
        finally:
            if self.driver:
                self.driver.quit()
                self.logger.info("Browser closed")

    def monitor_products(self):
        """Main monitoring loop for continuous operation"""
        self.logger.info("Starting continuous stock monitor...")
        last_status = {}
        
        while True:
            try:
                for product in self.config['products']:
                    product_name = product['name']
                    self.logger.info(f"Checking {product_name}...")
                    
                    is_in_stock, message = self.check_stock_status(product)
                    
                    if is_in_stock is None:
                        continue
                    
                    # Check if status changed from out of stock to in stock
                    if is_in_stock and last_status.get(product_name) in [None, False]:
                        self.logger.info(f"ALERT: {product_name} is NOW IN STOCK!")
                        self.send_notification(product_name, product['url'], message)
                        self.send_telegram_notification(product_name, product['url'], message)
                    elif is_in_stock:
                        self.logger.info(f"OK: {product_name} is in stock")
                    else:
                        self.logger.info(f"WAITING: {product_name} is out of stock")
                    
                    last_status[product_name] = is_in_stock
                    time.sleep(2)
                
                self.logger.info(f"Waiting {self.config['check_interval']} seconds before next check...")
                time.sleep(self.config['check_interval'])
                
            except KeyboardInterrupt:
                self.logger.info("Monitor stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error: {str(e)}")
                time.sleep(60)
        
        if self.driver:
            self.driver.quit()

if __name__ == "__main__":
    # Check if running as a single check (for cron/scheduled jobs)
    if os.getenv("SINGLE_CHECK", "false").lower() == "true":
        monitor = StockMonitor()
        monitor.run_single_check()
    else:
        monitor = StockMonitor()
        monitor.monitor_products()