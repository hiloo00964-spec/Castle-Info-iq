# Castle Info Bot - GitHub Actions

تم تحويل البوت من Flask + APScheduler إلى GitHub Actions.

أوقات التشغيل بتوقيت بغداد:
10:00، 12:00، 14:00، 16:00، 18:00، 20:00، 22:00.

Secrets المطلوبة:
BOT_TOKEN
TELEGRAM_CHANNEL_ID
GOOGLE_API_KEY
API_ID
API_HASH

اختياري:
GEMINI_MODEL

مهم: GitHub Actions يحفظ data.json إلى المستودع بعد التشغيل حتى تبقى حماية التكرار بين الـ runners.
