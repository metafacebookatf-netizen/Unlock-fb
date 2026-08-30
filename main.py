import os
import time
import logging
import pickle
from datetime import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pyvirtualdisplay import Display
from flask import Flask
import threading

# ============ CẤU HÌNH ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "THAY_BOT_TOKEN_O_DAY")
SESSION_FILE = "fb_session.pkl"

# ============ LOGGING ============
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ STATE ============
class BotState:
    def __init__(self):
        self.driver = None
        self.waiting_for = None
        self.current_step = 0
        self.lock_type = None
        self.flow_history = []
        self.session_dir = "session"
        self.fb_contact = None
        self.fb_password = None
        os.makedirs(self.session_dir, exist_ok=True)

    def log_step(self, step):
        self.flow_history.append(f"[{datetime.now().strftime('%H:%M:%S')}] {step}")
        self.current_step += 1

STATE = BotState()

# ============ DRIVER ============
def get_driver():
    if STATE.driver is not None:
        return STATE.driver
    try:
        display = Display(visible=0, size=(1280, 720))
        display.start()
        opts = uc.ChromeOptions()
        opts.add_argument("--window-size=1280,720")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-popup-blocking")
        driver = uc.Chrome(options=opts, user_data_dir=STATE.session_dir)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN', 'vi', 'en-US', 'en']});
            """
        })
        STATE.driver = driver
        return driver
    except Exception as e:
        logger.error(f"Lỗi khởi động driver: {e}")
        raise RuntimeError(f"Không thể khởi động trình duyệt: {e}")

# ============ DETECT LOCK TYPE ============
def detect_lock_type(driver):
    url = driver.current_url
    if "checkpoint" in url:
        return "checkpoint"
    elif "confirm" in url:
        return "confirm_identity"
    elif "recover" in url or "forgot" in url:
        return "forgot_password"
    elif "hacked" in url:
        return "hacked_recovery"
    elif "disabled" in url:
        return "disabled"
    else:
        try:
            if "you're temporarily blocked" in driver.page_source.lower():
                return "temp_blocked"
            elif "permanently" in driver.page_source.lower():
                return "perm_disabled"
            elif "suspended" in driver.page_source.lower():
                return "suspended"
        except:
            pass
    return "unknown"

# ============ FLOWS ============
def goto_login_recovery(driver):
    driver.get("https://www.facebook.com/login/identify")
    time.sleep(3)

def fill_contact(driver, contact):
    input_ = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    input_.clear()
    input_.send_keys(contact)
    driver.find_element(By.ID, "identify_button").click()
    time.sleep(3)
    STATE.log_step("Đã gửi thông tin liên hệ")

def fill_password(driver, password):
    input_ = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "pass")))
    input_.clear()
    input_.send_keys(password)
    driver.find_element(By.ID, "loginbutton").click()
    time.sleep(3)

def handle_checkpoint_flow(driver):
    STATE.waiting_for = "confirm"
    STATE.log_step("Checkpoint - cần xác nhận 'Đây là tôi'")
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Tôi') or contains(.,'This is me') or contains(.,'Đây là tôi')]"))
        )
        btn.click()
        time.sleep(2)
        new_pass = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "password_new")))
        new_pass.send_keys(STATE.fb_password)
        confirm = driver.find_element(By.NAME, "password_confirm")
        confirm.send_keys(STATE.fb_password)
        driver.find_element(By.ID, "checkpoint_challenge_submit").click()
        STATE.log_step("Đã đổi mật khẩu qua checkpoint")
    except Exception as e:
        logger.error(f"Checkpoint flow error: {e}")

def handle_forgot_password_flow(driver):
    STATE.waiting_for = "email_code"
    STATE.log_step("Đang chờ mã từ email/SMS")
    input_ = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    input_.send_keys(STATE.fb_contact)
    driver.find_element(By.ID, "identify_button").click()
    time.sleep(2)
    try:
        sms_btn = driver.find_element(By.XPATH, "//button[contains(.,'SMS') or contains(.,'Text')]")
        sms_btn.click()
    except:
        pass
    driver.find_element(By.ID, "continue_button").click()
    STATE.log_step("Đã gửi yêu cầu lấy mã")

def handle_hacked_flow(driver):
    driver.get("https://www.facebook.com/hacked")
    time.sleep(3)
    STATE.waiting_for = "confirm"
    STATE.log_step("Bắt đầu flow hacked - cần xác nhận danh tính")
    btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Tiếp tục') or contains(.,'Continue')]")))
    btn.click()
    time.sleep(2)

def handle_disabled_flow(driver):
    driver.get("https://www.facebook.com/help/contact/260749603972907")
    time.sleep(3)
    STATE.waiting_for = "id"
    STATE.log_step("Bị disable - cần upload giấy tờ tuỳ thân")

def upload_id_to_facebook(driver, id_path):
    try:
        file_input = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//input[@type='file']")))
        file_input.send_keys(os.path.abspath(id_path))
        time.sleep(2)
        btn = driver.find_element(By.XPATH, "//button[contains(.,'Gửi') or contains(.,'Submit') or contains(.,'Tiếp tục')]")
        btn.click()
        STATE.log_step("Đã gửi giấy tờ")
        STATE.waiting_for = "confirm"
    except Exception as e:
        logger.error(f"Upload ID error: {e}")

def handle_mfa_code(driver, code):
    input_ = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "code")))
    input_.clear()
    input_.send_keys(code)
    driver.find_element(By.ID, "check_button").click()
    STATE.log_step("Đã nhập mã 2FA")

def handle_email_code(driver, code):
    input_ = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "code")))
    input_.clear()
    input_.send_keys(code)
    driver.find_element(By.ID, "continue_button").click()
    STATE.log_step("Đã nhập mã xác nhận")

# ============ TELEGRAM HANDLERS ============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["🔓 Mở khoá Facebook"],
        ["📋 Trạng thái", "📸 Chụp màn hình"],
        ["📄 Upload giấy tờ", "❌ Huỷ"]
    ]
    await update.message.reply_text(
        "Bot mở khoá Facebook sẵn sàng.\n"
        "Nhấn 🔓 Mở khoá Facebook để bắt đầu.\n"
        "Bot sẽ hỏi số điện thoại/email và mật khẩu.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE.waiting_for = "ask_contact"
    await update.message.reply_text("Gửi số điện thoại hoặc email đăng nhập Facebook của bạn.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = "\n".join(STATE.flow_history[-10:]) if STATE.flow_history else "Chưa có gì"
    await update.message.reply_text(
        f"Bước hiện tại: {STATE.current_step}\n"
        f"Đang chờ: {STATE.waiting_for}\n"
        f"Loại khoá: {STATE.lock_type}\n"
        f"Lịch sử:\n{history}"
    )

async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if STATE.driver is None:
        await update.message.reply_text("Chưa có trình duyệt nào đang chạy.")
        return
    filename = take_screenshot(STATE.driver)
    with open(filename, "rb") as f:
        await update.message.reply_photo(f, caption="Màn hình hiện tại")

def take_screenshot(driver, filename="screenshot.png"):
    driver.save_screenshot(filename)
    return filename

async def cmd_upload_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        await file.download_to_drive("id_photo.jpg")
        await update.message.reply_text("Đã nhận ảnh. Đang upload lên Facebook...")
        if STATE.driver is None:
            await update.message.reply_text("Không có trình duyệt. Nhấn /unlock trước.")
            return
        upload_id_to_facebook(STATE.driver, "id_photo.jpg")
        await update.message.reply_text("Đã gửi ảnh lên Facebook.")
    else:
        await update.message.reply_text("Gửi ảnh dạng photo, không phải file.")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if STATE.driver is not None:
        STATE.driver.quit()
        STATE.driver = None
    STATE.waiting_for = None
    await update.message.reply_text("Đã huỷ toàn bộ phiên.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "🔓 Mở khoá Facebook":
        await cmd_unlock(update, context)
    elif text == "📋 Trạng thái":
        await cmd_status(update, context)
    elif text == "📸 Chụp màn hình":
        await cmd_screenshot(update, context)
    elif text == "📄 Upload giấy tờ":
        await cmd_upload_id(update, context)
    elif text == "❌ Huỷ":
        await cmd_cancel(update, context)
    else:
        # Nhận số điện thoại/email
        if STATE.waiting_for == "ask_contact":
            STATE.fb_contact = text.strip()
            STATE.waiting_for = "ask_password"
            await update.message.reply_text("Đã nhận. Gửi mật khẩu Facebook.")
        # Nhận mật khẩu và bắt đầu
        elif STATE.waiting_for == "ask_password":
            STATE.fb_password = text.strip()
            STATE.waiting_for = None
            await update.message.reply_text("Đang khởi động trình duyệt...")
            try:
                driver = get_driver()
                goto_login_recovery(driver)
                fill_contact(driver, STATE.fb_contact)
                time.sleep(2)
                STATE.lock_type = detect_lock_type(driver)
                await update.message.reply_text(f"Phát hiện trạng thái: {STATE.lock_type}")
                if "login" in driver.current_url:
                    try:
                        fill_password(driver, STATE.fb_password)
                    except Exception as e:
                        logger.error(f"Lỗi nhập mật khẩu: {e}")
                        await update.message.reply_text(f"Lỗi nhập mật khẩu: {e}")
                if STATE.lock_type in ["checkpoint", "temp_blocked", "suspended"]:
                    handle_checkpoint_flow(driver)
                elif STATE.lock_type == "forgot_password":
                    handle_forgot_password_flow(driver)
                elif STATE.lock_type == "hacked_recovery":
                    handle_hacked_flow(driver)
                elif STATE.lock_type in ["disabled", "perm_disabled"]:
                    handle_disabled_flow(driver)
                else:
                    handle_forgot_password_flow(driver)
                await update.message.reply_text(f"Đang ở bước: {STATE.current_step} | Chờ: {STATE.waiting_for}")
            except Exception as e:
                logger.error(f"Lỗi xử lý: {e}")
                await update.message.reply_text(f"Lỗi xử lý: {e}")
        # Nhập mã xác nhận
        elif STATE.waiting_for in ["code", "email_code"]:
            if STATE.driver is not None:
                url = STATE.driver.current_url
                if "checkpoint" in url:
                    handle_mfa_code(STATE.driver, text)
                else:
                    handle_email_code(STATE.driver, text)
            await update.message.reply_text("Đã nhập mã.")
            time.sleep(3)
            STATE.log_step("Đã xử lý mã, kiểm tra trạng thái mới")
            STATE.lock_type = detect_lock_type(STATE.driver)
            await update.message.reply_text(f"Trạng thái mới: {STATE.lock_type}")
        # Xác nhận danh tính
        elif STATE.waiting_for == "confirm":
            if text.lower() == "có" or text == "Yes":
                try:
                    btn = STATE.driver.find_element(By.XPATH, "//button[contains(.,'Đây là tôi') or contains(.,'This is me')]")
                    btn.click()
                    STATE.log_step("Đã xác nhận danh tính")
                except:
                    pass
        else:
            await update.message.reply_text("Lệnh không hợp lệ. Dùng /help hoặc chọn nút.")

# ============ FLASK WEB SERVER ============
app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

def run_web():
    app.run(host='0.0.0.0', port=10000)

# ============ MAIN ============
def main():
    threading.Thread(target=run_web, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("unlock", cmd_unlock))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("screenshot", cmd_screenshot))
    application.add_handler(CommandHandler("upload_id", cmd_upload_id))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.PHOTO, cmd_upload_id))
    logger.info("Bot đang chạy...")
    application.run_polling()

if __name__ == "__main__":
    main()
