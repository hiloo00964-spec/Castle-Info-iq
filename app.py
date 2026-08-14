import os,re,json,time,random,logging,html
from pathlib import Path
from datetime import datetime
from typing import Optional,Any,Dict,List,Tuple
import pytz,requests,feedparser
from google import genai
from bs4 import BeautifulSoup
from PIL import Image
from pyrogram import Client,enums

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
API_ID=int(os.getenv("API_ID","0") or 0)
API_HASH=os.getenv("API_HASH","").strip()
TELEGRAM_CHANNEL_ID=os.getenv("TELEGRAM_CHANNEL_ID","").strip()
TELEGRAM_CHANNEL_USERNAME=os.getenv("TELEGRAM_CHANNEL_USERNAME","@CastleInfoiq").strip()
GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY","").strip()
TIMEZONE=os.getenv("TIMEZONE","Asia/Baghdad").strip()
PREFERRED=os.getenv("GEMINI_MODEL","").strip()

DATA_FILE=Path("data.json")
BACKUP_FILE=Path("data.backup.json")
SIGNATURE="#قلعة_المعلومات_العامة\nاشتـــرك الآن :- https://t.me/CastleInfoiq"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"

RSS=[
{"name":"NASA","category":"فضاء","url":"https://www.nasa.gov/rss/dyn/breaking_news.rss","hashtags":"#فضاء #ناسا #الكون #معلومات_عامة"},
{"name":"NASA Science","category":"فضاء","url":"https://science.nasa.gov/feed/","hashtags":"#فضاء #علوم #الكون #معلومات_عامة"},
{"name":"ScienceDaily","category":"علوم","url":"https://www.sciencedaily.com/rss/top.xml","hashtags":"#علوم #اكتشافات #معرفة #معلومات_عامة"},
{"name":"ScienceDaily Space","category":"فضاء","url":"https://www.sciencedaily.com/rss/space_time.xml","hashtags":"#فضاء #علوم #الكون #معلومات_عامة"},
{"name":"ScienceDaily Matter","category":"علوم","url":"https://www.sciencedaily.com/rss/matter_energy.xml","hashtags":"#علوم #اكتشافات #تكنولوجيا #معلومات_عامة"},
{"name":"World History Encyclopedia","category":"تاريخ","url":"https://www.worldhistory.org/rss/","hashtags":"#تاريخ #حضارات #معرفة #معلومات_عامة"}]

FALLBACK=[
"قصة بناء الأهرامات وأسرارها العلمية","كيف غيّر نيوتن فهم البشر للكون",
"أغرب أسرار كوكب المشتري","قصة مكتبة الإسكندرية ولماذا بقيت لغزاً",
"كيف بدأت الثورة الصناعية وغيرت العالم","العلماء الذين غيروا مجرى التاريخ",
"أسرار الثقوب السوداء بطريقة مبسطة","حضارة وادي الرافدين وأثرها على العالم",
"كيف تعمل الذاكرة داخل دماغ الإنسان","اختراعات ظهرت بالصدفة وغيرت حياتنا",
"لماذا كان اكتشاف الكهرباء نقطة تحول في التاريخ","كيف عرف الإنسان الزمن قبل اختراع الساعة"]

def validate():
    missing=[n for n,v in {"BOT_TOKEN":BOT_TOKEN,"TELEGRAM_CHANNEL_ID":TELEGRAM_CHANNEL_ID,
        "GOOGLE_API_KEY":GOOGLE_API_KEY,"API_ID":API_ID,"API_HASH":API_HASH}.items() if not v]
    if missing: raise RuntimeError("Missing secrets: "+", ".join(missing))

def save(d):
    try:
        if DATA_FILE.exists(): BACKUP_FILE.write_text(DATA_FILE.read_text(encoding="utf8"),encoding="utf8")
    except: pass
    t=DATA_FILE.with_suffix(".tmp")
    t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf8"); t.replace(DATA_FILE)

def load():
    default={"posted_links":[],"posted_titles":[],"last_posts":[],"last_error":"","active_gemini_model":""}
    try:
        d=json.loads(DATA_FILE.read_text(encoding="utf8")) if DATA_FILE.exists() else default
        for k,v in default.items(): d.setdefault(k,v)
        return d
    except:
        try: return json.loads(BACKUP_FILE.read_text(encoding="utf8"))
        except: save(default); return default

def norm(s):
    return re.sub(r"\s+"," ",BeautifulSoup(s or "","html.parser").get_text(" ",strip=True)).strip()

def fetch(url,timeout=20):
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=timeout,allow_redirects=True)
        return r.text if r.status_code==200 and r.text else None
    except Exception as e:
        logging.warning("fetch: %s",e); return None

def image_of(e,url=""):
    c=[]
    for x in (getattr(e,"media_content",[]) or [])+(getattr(e,"media_thumbnail",[]) or []):
        if x.get("url"): c.append(x["url"])
    for k in ("image","thumbnail"):
        if e.get(k): c.append(e[k])
    try:
        s=BeautifulSoup(e.get("summary","") or e.get("description",""),"html.parser")
        if s.find("img") and s.find("img").get("src"): c.append(s.find("img")["src"])
    except: pass
    h=fetch(url,12) if url else None
    if h:
        s=BeautifulSoup(h,"html.parser")
        for a,v in [("property","og:image"),("name","twitter:image")]:
            x=s.find("meta",attrs={a:v})
            if x and x.get("content"): c.append(x["content"])
    return next((x for x in c if isinstance(x,str) and x.startswith("http")),None)

def article_text(e,url):
    p=[norm(e.get(k,"")) for k in ("title","summary","description") if e.get(k)]
    h=fetch(url,15)
    if h:
        s=BeautifulSoup(h,"html.parser")
        for x in s(["script","style","noscript","header","footer","nav","aside"]): x.decompose()
        a=s.find("article") or s.find("main") or s.body
        if a:
            q=[norm(x.get_text(" ",strip=True)) for x in a.find_all("p")]
            p += [x for x in q if len(x)>45][:9]
    return norm(" ".join(p))[:6000]

def candidate(d):
    sources=RSS[:]; random.shuffle(sources)
    links=set(d.get("posted_links",[])); titles=set(d.get("posted_titles",[]))
    for src in sources:
        try: entries=list(feedparser.parse(src["url"]).entries or [])
        except Exception as e: logging.warning("RSS: %s",e); continue
        random.shuffle(entries)
        for e in entries[:14]:
            link=(e.get("link","") or "").strip(); title=norm(e.get("title",""))[:180]
            if not link or not title or link in links or title in titles: continue
            text=article_text(e,link)
            if len(text)<220: continue
            return {"mode":"rss","source":src["name"],"category":src["category"],"hashtags":src["hashtags"],
                    "title":title,"link":link,"text":text,"image_url":image_of(e,link)}
    return None


def _model_name(model: Any) -> str:
    return (getattr(model, "name", "") or "").removeprefix("models/")


def _model_supports_text_generation(model: Any) -> bool:
    """
    Accept only models whose live API metadata explicitly supports
    generateContent. supported_actions is the current SDK field; the older
    supported_generation_methods field remains only as a compatibility fallback.
    """
    actions = getattr(model, "supported_actions", None)
    if actions is None:
        actions = getattr(model, "supported_generation_methods", None)
    if not actions:
        return False
    normalized = {
        str(action).lower().replace("_", "")
        for action in actions
    }
    if "generatecontent" not in normalized:
        return False
    name = _model_name(model).lower()
    blocked = (
        "embedding",
        "embed",
        "tts",
        "audio",
        "live",
        "veo",
        "imagen",
        "image",
        "robotics",
    )
    return not any(token in name for token in blocked)

def _model_rank(name: str) -> tuple:
    """
    Prefer stable text/Flash models but never require a hard-coded model ID.
    If Google adds a newer model, it is discovered automatically.
    """
    n = name.lower()

    preview_penalty = 20 if "preview" in n else 0
    flash_penalty = 0 if "flash" in n else 8
    lite_penalty = 0 if "lite" in n else 2
    experimental_penalty = 10 if any(
        token in n for token in ("experimental", "-exp", "_exp")
    ) else 0

    version_matches = re.findall(
        r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)",
        n
    )
    if version_matches:
        version = tuple(
            -int(part)
            for part in version_matches[-1].split(".")
        )
    else:
        version = (0,)

    return (
        preview_penalty,
        flash_penalty,
        lite_penalty,
        experimental_penalty,
        version,
        n,
    )


def discover_text_models(client: genai.Client) -> List[str]:
    """
    Ask Google's live model catalog on every run.

    No fixed Gemini model list is required. The API decides what models are
    currently available to this API key; the bot filters for text generation.
    """
    discovered = []

    try:
        for model in client.models.list():
            if not _model_supports_text_generation(model):
                continue

            name = _model_name(model)
            if name:
                discovered.append(name)

    except Exception as exc:
        raise RuntimeError(
            f"Gemini model discovery failed: {exc}"
        ) from exc

    discovered = list(dict.fromkeys(discovered))

    if PREFERRED:
        preferred = PREFERRED.removeprefix("models/")
        if preferred in discovered:
            discovered.remove(preferred)
            discovered.insert(0, preferred)
        else:
            logging.warning(
                "GEMINI_MODEL=%s is not currently available for text generation; ignoring it.",
                preferred,
            )

    discovered.sort(key=_model_rank)

    logging.info(
        "Gemini text models discovered dynamically: %s",
        discovered,
    )

    if not discovered:
        raise RuntimeError(
            "No currently available Gemini text-generation model was found."
        )

    return discovered


def generate_with_auto_model(
    prompt: str,
    data: Dict[str, Any]
) -> Tuple[str, str]:
    """
    Discover and try current text-capable Gemini models at runtime.

    If one model is unavailable, deprecated, quota-limited, or otherwise
    fails, automatically falls back to another currently listed model.
    """
    client = genai.Client(api_key=GOOGLE_API_KEY)

    candidates = discover_text_models(client)
    last_error = None

    for name in candidates:
        try:
            logging.info(
                "Trying dynamically discovered Gemini model: %s",
                name,
            )

            response = client.models.generate_content(
                model=name,
                contents=prompt,
            )

            result = (
                getattr(response, "text", "") or ""
            ).strip()

            result = re.sub(
                r"\n{3,}",
                "\n\n",
                result,
            ).replace("**", "").strip()

            if len(result) < 80:
                raise RuntimeError(
                    f"Gemini response too short from {name}"
                )

            data["active_gemini_model"] = name
            save(data)

            logging.info(
                "Gemini model success: %s",
                name,
            )

            return result, name

        except Exception as exc:
            last_error = exc

            logging.warning(
                "Gemini model failed: %s | %s",
                name,
                exc,
            )

            # Give the next currently available model a chance.
            time.sleep(2)

    raise RuntimeError(
        "All currently discovered Gemini text models failed. "
        f"Last error: {last_error}"
    )


def article_prompt(a):
    return f"""أنت محرر قناة تيليجرام عربية اسمها قلعة المعلومات العامة.
اكتب المنشور النهائي فقط باللغة العربية.
السطر الأول عنوان جذاب قصير.
بعده ملخص قصصي مشوق من 4 إلى 8 أسطر، بدون تعداد.
لا تذكر المصدر ولا رابط القناة.
لا تخترع معلومة غير موجودة.
اختم بمعلومة قوية أو إحساس دهشة.
آخر سطر فقط 3 إلى 6 هاشتاكات مناسبة.
لا تضف #قلعة_المعلومات_العامة ولا التوقيع.
الطول قبل الهاشتاكات لا يتجاوز 900 حرف.

التصنيف: {a['category']}
العنوان: {a['title']}
الهاشتاكات المقترحة: {a['hashtags']}
المادة:
{a['text']}"""

def fallback_prompt(t):
    return f"""اكتب منشور تيليجرام عربي عن: {t}
عنوان جذاب، ثم قصة مشوقة من 4 إلى 8 أسطر بدون تعداد، ثم 3 إلى 6 هاشتاكات في آخر سطر.
لا تذكر المصدر ولا رابط القناة ولا التوقيع. لا تخترع أرقاماً أو حقائق دقيقة غير مؤكدة.
لا يتجاوز النص قبل الهاشتاكات 900 حرف."""

def leak(x):
    bad=["Key points:","Story summary","Editor for a Telegram channel","Transform a provided","No bullet points"]
    return any(x.lower().find(y.lower())>=0 for y in bad)

def build(post):
    lines=[x.strip() for x in re.sub(r"\n{3,}","\n\n",post.replace("**","")).splitlines() if x.strip()]
    title=lines[0] if lines else "معلومة جديدة تستحق أن تعرفها"
    hs=[x for x in lines[1:] if "#" in x]
    body=[x for x in lines[1:] if "#" not in x]
    tags=re.findall(r"#\S+"," ".join(hs))
    tags=[x for x in tags if x!="#قلعة_المعلومات_العامة"][:6] or ["#علوم","#معرفة","#معلومات_عامة"]
    body="\n\n".join(body) or "تفاصيل هذه المعلومة تكشف جانباً مثيراً من عالم المعرفة، وتفتح أمام القارئ سؤالاً جديداً."
    tail="\n\n━━━━━━━━━━━━\n\nلأنك تستحق أن تعرف\n\n"+" ".join(tags)+"\n\n"+SIGNATURE
    room=max(250,940-len(title)-len(tail))
    if len(body)>room: body=body[:room].rstrip()+"..."
    return f"<b>{html.escape(title)}</b>\n\n<b>{html.escape(body)}</b>{tail}"

def download(url):
    if not url: return None
    raw=Path(f"raw_{int(time.time()*1000)}"); jpg=Path(f"image_{int(time.time()*1000)}.jpg")
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=30)
        if r.status_code!=200 or not r.content: return None
        raw.write_bytes(r.content)
        with Image.open(raw) as im:
            im=im.convert("RGB")
            if max(im.size)>2000:
                ratio=2000/max(im.size); im=im.resize((int(im.width*ratio),int(im.height*ratio)))
            im.save(jpg,"JPEG",quality=88,optimize=True)
        if jpg.stat().st_size>8_000_000:
            with Image.open(jpg) as im: im.convert("RGB").save(jpg,"JPEG",quality=72,optimize=True)
        return jpg if jpg.exists() and jpg.stat().st_size>1000 else None
    except Exception as e: logging.warning("image: %s",e); return None
    finally:
        try: raw.unlink(missing_ok=True)
        except: pass

def telegram(caption,img=None):
    p=download(img) if img else None
    try:
        with Client("castle_info_bot",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN,in_memory=True) as app:
            me = app.get_me()
            bot_username = f"@{me.username}" if getattr(me, "username", None) else "<no username>"
            logging.info(
                "Telegram identity verified: %s (ID: %s)",
                bot_username,
                getattr(me, "id", "unknown"),
            )
            logging.info(
                "Telegram target configured: username=%s id=%s",
                TELEGRAM_CHANNEL_USERNAME,
                TELEGRAM_CHANNEL_ID,
            )

            # GitHub Actions starts with no local Pyrogram peer cache. Resolve the
            # public username first so Telegram supplies the channel access hash.
            try:
                chat = app.get_chat(TELEGRAM_CHANNEL_USERNAME)
            except Exception as username_exc:
                logging.warning(
                    "Unable to resolve Telegram channel by username %s: %s",
                    TELEGRAM_CHANNEL_USERNAME,
                    type(username_exc).__name__,
                )
                try:
                    chat = app.get_chat(int(TELEGRAM_CHANNEL_ID))
                except Exception as id_exc:
                    raise RuntimeError(
                        "Telegram channel could not be resolved. Confirm that "
                        "BOT_TOKEN belongs to the bot that is an administrator of "
                        f"{TELEGRAM_CHANNEL_USERNAME}, and that the configured "
                        "channel username and ID are correct. "
                        f"Username error={type(username_exc).__name__}; "
                        f"ID error={type(id_exc).__name__}."
                    ) from id_exc

            chat_id = getattr(chat, "id", None)
            expected_channel_id = int(TELEGRAM_CHANNEL_ID)
            if chat_id != expected_channel_id:
                raise RuntimeError(
                    "Resolved Telegram channel does not match the configured ID: "
                    f"expected {expected_channel_id}, got {chat_id}."
                )
            logging.info(
                "Telegram channel resolved: title=%s id=%s",
                getattr(chat, "title", ""),
                chat_id,
            )

            try:
                membership = app.get_chat_member(chat_id, me.id)
                status = getattr(membership, "status", None)
                privileges = getattr(membership, "privileges", None)
                can_post = (
                    status == enums.ChatMemberStatus.OWNER
                    or (
                        status == enums.ChatMemberStatus.ADMINISTRATOR
                        and bool(getattr(privileges, "can_post_messages", False))
                    )
                )
            except Exception as membership_exc:
                raise RuntimeError(
                    "Telegram channel was resolved, but the bot membership and "
                    "posting permission could not be verified. Ensure the bot is "
                    "an administrator with permission to post messages. "
                    f"Verification error={type(membership_exc).__name__}."
                ) from membership_exc
            if not can_post:
                raise RuntimeError(
                    "Telegram bot is not an administrator with permission to post "
                    f"messages in {TELEGRAM_CHANNEL_USERNAME}."
                )
            logging.info("Telegram posting permission verified")

            if p and p.exists():
                try:
                    app.send_photo(
                        chat_id,
                        str(p),
                        caption=caption,
                        parse_mode=enums.ParseMode.HTML
                    )
                    logging.info("Posted photo to Telegram successfully")
                    return
                except Exception as e:
                    logging.warning(
                        "Photo publish failed, falling back to text: %s",
                        e
                    )
            app.send_message(
                chat_id,
                caption,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True
            )
            logging.info("Posted text to Telegram successfully")
    finally:
        if p:
            try:
                p.unlink(missing_ok=True)
            except:
                pass

def remember(d,a,caption):
    d.setdefault("posted_links",[]).append(a.get("link",""))
    d.setdefault("posted_titles",[]).append(a.get("title",""))
    d["posted_links"]=d["posted_links"][-700:]; d["posted_titles"]=d["posted_titles"][-700:]
    d.setdefault("last_posts",[]).append({"time":datetime.now(pytz.timezone(TIMEZONE)).isoformat(),"title":a.get("title",""),
        "source":a.get("source",""),"category":a.get("category",""),"link":a.get("link",""),"image":bool(a.get("image_url"))})
    d["last_posts"]=d["last_posts"][-40:]; d["last_error"]=""; save(d)

def run():
    d=load()
    try:
        a=candidate(d)
        if a:
            text=generate_with_auto_model(article_prompt(a), d)[0]
        else:
            t=random.choice([x for x in FALLBACK if x not in set(d.get("posted_titles",[]))] or FALLBACK)
            text=generate_with_auto_model(fallback_prompt(t), d)[0]
            a={"mode":"fallback","source":"Gemini","category":"معلومات عامة","title":t,"link":"fallback:"+t,"image_url":None}
        if leak(text): raise RuntimeError("Gemini returned instruction/prompt text")
        caption=build(text); telegram(caption,a.get("image_url")); remember(d,a,caption)
        logging.info("SUCCESS")
    except Exception as e:
        d["last_error"]=f"{datetime.now().isoformat()} | {e}"; save(d); raise

if __name__=="__main__":
    validate()
    run()
