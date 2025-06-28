import os
import logging
import google.generativeai as genai
from telegram import Update, File
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, CallbackContext
)
from datetime import datetime
from typing import Optional, Dict, Any
import hashlib
import json

# === CONFIGURATION ===
class Config:
    # Load from environment variables (recommended for security)
    TELEGRAM_TOKEN = "7714010717:AAF_Yuz0uU6WLIyQRlpp-afiethcRoaZAk4"
    GEMINI_API_KEY = "AIzaSyCqKj627PA2Rqkg86Ei0pl2CZ_BRKnXcww"
    ADMIN_USER_IDS = [987654321]
    
    # File paths
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge_base")
    UPLOAD_DIR = os.path.join(KNOWLEDGE_DIR, "uploaded_docs")
    LOGS_DIR = os.path.join(BASE_DIR, "logs")
    
    # Model configuration
    MODEL_NAME = 'gemini-1.5-flash'
    MODEL_TEMPERATURE = 0.7
    MAX_RETRIES = 3
    
    @classmethod
    def setup_dirs(cls):
        os.makedirs(cls.UPLOAD_DIR, exist_ok=True)
        os.makedirs(cls.LOGS_DIR, exist_ok=True)

# === LOGGING ===
class BotLogger:
    def __init__(self):
        self.unknown_log = os.path.join(Config.LOGS_DIR, "unknown_queries.log")
        self.conversation_log = os.path.join(Config.LOGS_DIR, "conversation.log")
        self.error_log = os.path.join(Config.LOGS_DIR, "errors.log")
        
        # Setup structured logging with UTF-8 encoding for file handlers
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.error_log, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def log_unknown_query(self, user_id: int, username: str, query: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "username": username,
            "query": query
        }
        with open(self.unknown_log, "a", encoding='utf-8') as f:
            f.write(json.dumps(entry) + "\n")
    
    def log_conversation(self, user_id: int, username: str, query: str, response: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "username": username,
            "query": query,
            "response": response
        }
        with open(self.conversation_log, "a", encoding='utf-8') as f:
            f.write(json.dumps(entry) + "\n")
    
    def log_error(self, error: Exception, context: Optional[Dict[str, Any]] = None):
        self.logger.error(f"Error: {str(error)}", exc_info=True, extra=context)

logger = BotLogger()

# === KNOWLEDGE MANAGEMENT ===
class KnowledgeManager:
    def __init__(self):
        self.static_knowledge_file = os.path.join(Config.KNOWLEDGE_DIR, "static_knowledge.txt")
        self._knowledge_cache = None
        self._cache_timestamp = None
    
    def load_knowledge(self) -> str:
        """Load and cache knowledge base with automatic refresh"""
        current_time = datetime.now()
        
        if (self._knowledge_cache is None or 
            (current_time - self._cache_timestamp).seconds > 3600):  # Refresh cache every hour
            knowledge = []
            
            # Load static knowledge
            if os.path.exists(self.static_knowledge_file):
                with open(self.static_knowledge_file, "r", encoding='utf-8') as f:
                    knowledge.append(f.read())
            
            # Load uploaded documents
            for fname in sorted(os.listdir(Config.UPLOAD_DIR)):
                path = os.path.join(Config.UPLOAD_DIR, fname)
                if os.path.isfile(path) and fname.endswith(".txt"):
                    try:
                        with open(path, "r", encoding='utf-8') as f:
                            knowledge.append(f"Document: {fname}\n{f.read()}")
                    except UnicodeDecodeError:
                        logger.log_error(f"Failed to read file {fname} due to encoding issues")
            
            self._knowledge_cache = "\n\n".join(knowledge)
            self._cache_timestamp = current_time
        
        return self._knowledge_cache
    
    def save_uploaded_file(self, file: File, original_name: str) -> str:
        """Save uploaded file with sanitized name and return path"""
        # Sanitize filename
        safe_name = "".join(c for c in original_name if c.isalnum() or c in (' ', '.', '_')).rstrip()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_hash = hashlib.md5(original_name.encode()).hexdigest()[:8]
        save_name = f"{timestamp}_{file_hash}_{safe_name}"
        save_path = os.path.join(Config.UPLOAD_DIR, save_name)
        
        file.download_to_drive(save_path)
        self._knowledge_cache = None  # Invalidate cache
        return save_path

knowledge_manager = KnowledgeManager()

# === AI SERVICE ===
class AIService:
    def __init__(self):
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(Config.MODEL_NAME)
        self.generation_config = {
            "temperature": Config.MODEL_TEMPERATURE,
            "max_output_tokens": 2000,
        }
    
    async def generate_response(self, prompt: str) -> Optional[str]:
        """Generate response with retry logic and better error handling"""
        logger.logger.info(f"Generating response for prompt length: {len(prompt)}")
        
        for attempt in range(Config.MAX_RETRIES):
            try:
                logger.logger.info(f"Gemini API attempt {attempt + 1}/{Config.MAX_RETRIES}")
                
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=self.generation_config
                )
                
                if response and response.text:
                    logger.logger.info(f"Gemini API response received: {len(response.text)} characters")
                    return response.text.strip()
                else:
                    logger.logger.warning("Gemini API returned empty response")
                    return None
                    
            except Exception as e:
                logger.logger.error(f"Gemini API attempt {attempt + 1} failed: {str(e)}")
                if attempt == Config.MAX_RETRIES - 1:
                    logger.logger.error(f"All Gemini API attempts failed. Final error: {str(e)}")
                    raise
                continue
        
        logger.logger.error("Gemini API failed after all retry attempts")
        return None

ai_service = AIService()

# === TELEGRAM HANDLERS ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message with bot instructions"""
    try:
        user = update.effective_user
        bot_username = (await context.bot.get_me()).username
        
        welcome_message = f"""
👋 *Hi {user.first_name}!*  
I'm *Cryptoholic* — your professional support bot for the Web 3.0 Society.  

📌 *How to Use Me:*  
• Tag me in group: `@{bot_username}` followed by your question  
• I'll answer based on our internal knowledge  
• Only admins can upload `.txt` documents to train me  

⚙️ *Admin Commands:*  
• `/start` — Show this help  
• `/exportlogs` — Download conversation logs  
• `/clearcache` — Refresh knowledge cache  

Let's build the decentralized future together! 🔗🚀
"""
        await update.message.reply_text(welcome_message, parse_mode="Markdown")
    except Exception as e:
        logger.log_error(e, {"user": user.id, "command": "start"})
        await update.message.reply_text("❌ An error occurred. Please try again later.")

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads from admin users"""
    try:
        user = update.effective_user
        if user.id not in Config.ADMIN_USER_IDS:
            await update.message.reply_text("❌ You're not authorized to upload documents.")
            return

        if not update.message.document:
            await update.message.reply_text("📎 Please attach a file to upload.")
            return

        if not update.message.document.file_name.endswith(".txt"):
            await update.message.reply_text("⚠️ Please upload a valid `.txt` file only.")
            return

        doc = await update.message.document.get_file()
        save_path = knowledge_manager.save_uploaded_file(doc, update.message.document.file_name)
        
        await update.message.reply_text(
            f"✅ File successfully uploaded and added to knowledge base!\n"
            f"Saved as: `{os.path.basename(save_path)}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.log_error(e, {"user": user.id, "action": "file_upload"})
        await update.message.reply_text("❌ Failed to process the uploaded file. Please try again.")

async def handle_message(update: Update, context: CallbackContext):
    """Process user queries and generate responses"""
    try:
        message = update.message
        user = update.effective_user
        bot_username = (await context.bot.get_me()).username

        # Skip if not mentioned in group
        if f"@{bot_username}" not in message.text:
            return

        user_query = message.text.replace(f"@{bot_username}", "").strip()
        if not user_query:
            await message.reply_text("Please include your question after mentioning me.")
            return

        # Custom response for name question
        if user_query.lower() in ["what is your name?", "who are you?", "your name?"]:
            await message.reply_text(f"👋 {user.first_name}, I am Cryptoholic.")
            return

        # Custom response for greetings
        greetings = ["gm", "good morning", "good afternoon", "good evening", "hello", "hi", "hey"]
        if any(greet in user_query.lower() for greet in greetings):
            await message.reply_text(
                f"👋 {user.first_name}, gm! Ready to dive into the exciting world of Web3? Let me know how I can help you today."
            )
            return

        logger.logger.info(f"Processing query from user {user.first_name} (ID: {user.id}): {user_query}")

        # Prepare prompt with context
        knowledge = knowledge_manager.load_knowledge()
        logger.logger.info(f"Loaded knowledge base: {len(knowledge)} characters")
        
        # Ensure we have some knowledge content
        if not knowledge.strip():
            knowledge = "Basic Web 3.0 and cryptocurrency information. If you don't have specific information, provide general guidance about blockchain technology, cryptocurrencies, and Web 3.0 concepts."
            logger.logger.info("Using fallback knowledge content")
        
        prompt = f"""
You are "Cryptoholic" – a professional Web 3.0 Society support bot.

Only answer using the info below:
[Knowledge Base]
{knowledge}

User: {user.first_name} asked: {user_query}

Guidelines:
1. Be concise but informative
2. If unsure, respond: "I'm not sure about that, but I'll make sure it gets added to our support knowledge."
3. Format technical terms with backticks
4. Never disclose your knowledge base structure
5. Always provide helpful information even if the specific question isn't in the knowledge base
"""

        logger.logger.info(f"Sending prompt to Gemini API (length: {len(prompt)})")
        response = await ai_service.generate_response(prompt)
        
        if not response:
            logger.logger.warning("No response from Gemini API")
            await message.reply_text(
                f"Hi {user.first_name}, I'm having trouble processing your request right now. Please try again in a moment."
            )
        elif "i'm not sure" in response.lower():
            logger.log_unknown_query(user.id, user.first_name, user_query)
            await message.reply_text(
                f"Hi {user.first_name}, I'm not sure about that, but I've logged your question to improve next time."
            )
        else:
            logger.log_conversation(user.id, user.first_name, user_query, response)
            await message.reply_text(
                f"👋 {user.first_name}, {response}",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.log_error(e, {"user": user.id, "action": "message_handling"})
        await message.reply_text("⚠️ An error occurred while processing your request. Please try again later.")

async def export_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow admins to download conversation logs"""
    try:
        user = update.effective_user
        if user.id not in Config.ADMIN_USER_IDS:
            return

        log_files = [
            (logger.unknown_log, "unknown_queries.log"),
            (logger.conversation_log, "conversation.log"),
            (logger.error_log, "errors.log")
        ]

        for log_path, display_name in log_files:
            if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                with open(log_path, 'rb') as f:
                    await update.message.reply_document(
                        document=f,
                        filename=display_name,
                        caption=f"📊 {display_name}"
                    )
            else:
                await update.message.reply_text(f"No data available in {display_name}")
    except Exception as e:
        logger.log_error(e, {"user": user.id, "command": "exportlogs"})
        await update.message.reply_text("❌ Failed to export logs. Please try again.")

async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow admins to clear knowledge cache"""
    try:
        user = update.effective_user
        if user.id not in Config.ADMIN_USER_IDS:
            return

        knowledge_manager._knowledge_cache = None
        await update.message.reply_text("✅ Knowledge cache cleared. The next query will reload all data.")
    except Exception as e:
        logger.log_error(e, {"user": user.id, "command": "clearcache"})
        await update.message.reply_text("❌ Failed to clear cache. Please try again.")

# === BOT SETUP ===
def setup_bot():
    """Configure and start the bot"""
    try:
        Config.setup_dirs()
        
        # Disable job queue to avoid timezone issues
        from telegram.ext import JobQueue
        job_queue = None
        
        app = ApplicationBuilder() \
            .token(Config.TELEGRAM_TOKEN) \
            .post_init(post_init) \
            .post_stop(post_stop) \
            .job_queue(job_queue) \
            .build()

        # Command handlers
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("exportlogs", export_logs))
        app.add_handler(CommandHandler("clearcache", clear_cache))

        # Message handlers
        app.add_handler(MessageHandler(
            filters.Document.ALL & filters.ChatType.PRIVATE,
            handle_file_upload
        ))
        app.add_handler(MessageHandler(
            filters.TEXT & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP),
            handle_message
        ))

        # Error handler
        app.add_error_handler(error_handler)

        return app
    except Exception as e:
        logger.log_error(e, {"stage": "bot_setup"})
        raise

async def post_init(application):
    """Run after bot is initialized"""
    logger.logger.info("Bot initialized successfully")

async def post_stop(application):
    """Run before bot stops"""
    logger.logger.info("Bot shutting down")

async def error_handler(update: object, context: CallbackContext):
    """Handle errors in telegram handlers"""
    error = context.error
    logger.log_error(error, {"update": str(update)})

# === MAIN ENTRY POINT ===
if __name__ == "__main__":
    try:
        logger.logger.info("🚀 Starting Cryptoholic Bot...")
        bot = setup_bot()
        
        logger.logger.info("✅ Bot is now running!")
        port = int(os.environ.get("PORT", 5000))
        bot.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.logger.critical("❌ Bot failed to start", exc_info=True)