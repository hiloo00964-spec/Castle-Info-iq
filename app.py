import os,re,json,time,random,logging,html,calendar
from pathlib import Path
from datetime import datetime
from typing import Optional,Any,Dict,List,Tuple
import pytz,requests,feedparser
from google import genai
from bs4 import BeautifulSoup
from PIL import Image,UnidentifiedImageError
from pyrogram import Client,enums
from pyrogram.errors import RPCError

logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
API_ID=int(os.getenv("API_ID","0") or 0)
API_HASH=os.getenv("API_HASH","").strip()
TELEGRAM_CHANNEL_ID=os.getenv("TELEGRAM_CHANNEL_ID","").strip()
TELEGRAM_CHANNEL_USERNAME=os.getenv("TELEGRAM_CHANNEL_USERNAME","@CastleInfoiq").strip()
GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY","").strip()
TIMEZONE=os.getenv("TIMEZONE","Asia/Baghdad").strip()
PREFERRED=os.getenv("GEMINI_MODEL","").strip()
RSS_TIMEOUT=15
RSS_RETRIES=2
ARTICLE_TEXT_LIMIT=3600
NON_NEWS_TERMS=("event","seminar","workshop","activity","poster","call for proposals")

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
    if DATA_FILE.exists():
        try:
            BACKUP_FILE.write_text(
                DATA_FILE.read_text(encoding="utf8"),
                encoding="utf8",
            )
        except (OSError, UnicodeError) as exc:
            logging.warning("State backup failed: %s", exc)
    t=DATA_FILE.with_suffix(".tmp")
    t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf8")
    t.replace(DATA_FILE)

def load():
    default={"posted_links":[],"posted_titles":[],"last_posts":[],"last_error":"",
             "active_gemini_model":"","gemini_model_fallbacks":[],
             "gemini_model_preference":""}
    try:
        d=json.loads(DATA_FILE.read_text(encoding="utf8")) if DATA_FILE.exists() else default
        if not isinstance(d,dict): raise ValueError("state is not an object")
        for k,v in default.items(): d.setdefault(k,v)
        return d
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        logging.warning("Primary state load failed: %s", exc)
        try:
            d=json.loads(BACKUP_FILE.read_text(encoding="utf8"))
            if not isinstance(d,dict): raise ValueError("backup state is not an object")
            for k,v in default.items(): d.setdefault(k,v)
            return d
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as backup_exc:
            logging.warning("Backup state load failed: %s", backup_exc)
            save(default)
            return default

def norm(s):
    return re.sub(r"\s+"," ",BeautifulSoup(s or "","html.parser").get_text(" ",strip=True)).strip()

def fetch(url,timeout=20):
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=timeout,allow_redirects=True)
        return r.text if r.status_code==200 and r.text else None
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.warning("fetch failed: url=%s reason=%s",url,exc)
        return None

def image_of(e,url=""):
    c=[]
    for x in (getattr(e,"media_content",[]) or [])+(getattr(e,"media_thumbnail",[]) or []):
        if x.get("url"): c.append(x["url"])
    for k in ("image","thumbnail"):
        if e.get(k): c.append(e[k])
    try:
        s=BeautifulSoup(e.get("summary","") or e.get("description",""),"html.parser")
        if s.find("img") and s.find("img").get("src"): c.append(s.find("img")["src"])
    except (AttributeError, TypeError, ValueError) as exc:
        logging.warning("image metadata parse failed: %s",exc)
    h=fetch(url,12) if url else None
    if h:
        s=BeautifulSoup(h,"html.parser")
        for a,v in [("property","og:image"),("name","twitter:image")]:
            x=s.find("meta",attrs={a:v})
            if x and x.get("content"): c.append(x["content"])
    return next((x for x in c if isinstance(x,str) and x.startswith("http")),None)

def compact_text(text,limit=ARTICLE_TEXT_LIMIT):
    text=norm(text)
    if len(text)<=limit: return text
    parts=re.split(r"(?<=[.!?؟])\s+",text)
    result=[]
    size=0
    for part in parts:
        if not part: continue
        extra=len(part)+(1 if result else 0)
        if size+extra>limit: break
        result.append(part)
        size+=extra
    return " ".join(result) or text[:limit].rstrip()

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
    return compact_text(" ".join(p))

def rss_entries(src):
    last_error=None
    for attempt in range(1,RSS_RETRIES+1):
        try:
            response=requests.get(
                src["url"],headers={"User-Agent":UA},timeout=RSS_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            if not response.content:
                raise ValueError("empty response")
            parsed=feedparser.parse(response.content)
            entries=list(parsed.entries or [])
            if getattr(parsed,"bozo",False) and not entries:
                raise ValueError(str(getattr(parsed,"bozo_exception","invalid feed")))
            logging.info("RSS source OK: %s entries=%d",src["name"],len(entries))
            return entries
        except (requests.RequestException, ValueError, TypeError, UnicodeError) as exc:
            last_error=exc
            logging.warning(
                "RSS source failed: %s attempt=%d/%d reason=%s",
                src["name"],attempt,RSS_RETRIES,exc,
            )
            if attempt<RSS_RETRIES: time.sleep(1)
    logging.error("RSS source unavailable: %s reason=%s",src["name"],last_error)
    return []

def entry_timestamp(e):
    for key in ("published_parsed","updated_parsed"):
        value=e.get(key)
        if value:
            try: return calendar.timegm(value)
            except (TypeError, ValueError, OverflowError): pass
    return 0

def is_non_news(e):
    haystack=" ".join(norm(e.get(k,"")) for k in ("title","summary","description")).casefold()
    return any(term in haystack for term in NON_NEWS_TERMS)

def candidate(d):
    links={str(x).strip() for x in d.get("posted_links",[]) if x}
    titles={norm(str(x)).casefold() for x in d.get("posted_titles",[]) if x}
    ranked=[]
    for src in RSS:
        for e in rss_entries(src):
            link=(e.get("link","") or "").strip()
            title=norm(e.get("title",""))[:180]
            if not link or not title or link in links or title.casefold() in titles:
                continue
            if is_non_news(e):
                logging.info("Skipping non-news RSS item: source=%s title=%s",src["name"],title)
                continue
            ranked.append((entry_timestamp(e),e,src))
    ranked.sort(key=lambda item:item[0],reverse=True)
    for _,e,src in ranked[:14*len(RSS)]:
        link=(e.get("link","") or "").strip()
        title=norm(e.get("title",""))[:180]
        text=article_text(e,link)
        if len(text)<220:
            logging.info("Skipping short RSS item: source=%s title=%s",src["name"],title)
            continue
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
    Ask Google's live model catalog only when the local model cache is empty
    or invalidated after failed cached models.

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


def cached_model_candidates(client: genai.Client, data: Dict[str, Any]) -> List[str]:
    cached=[str(x).removeprefix("models/") for x in data.get("gemini_model_fallbacks",[]) if x]
    preferred=PREFERRED.removeprefix("models/") if PREFERRED else ""
    cache_usable=bool(cached) and data.get("gemini_model_preference","")==preferred
    if not cache_usable:
        discovered=discover_text_models(client)
        ordered=[]
        if preferred and preferred in discovered:
            ordered.append(preferred)
        ordered.extend(x for x in discovered if x not in ordered)
        cached=ordered[:2]
        data["gemini_model_fallbacks"]=cached
        data["gemini_model_preference"]=preferred
        save(data)
    if not cached:
        raise RuntimeError("No cached Gemini text-generation model is available")
    return cached[:2]

def generate_with_auto_model(
    prompt: str,
    data: Dict[str, Any]
) -> Tuple[str, str]:
    """Use the cached preferred model and at most one local fallback."""
    client = genai.Client(api_key=GOOGLE_API_KEY)
    candidates=cached_model_candidates(client,data)
    last_error=None
    for index,name in enumerate(candidates):
        try:
            logging.info("Trying Gemini model %d/%d: %s",index+1,len(candidates),name)
            response=client.models.generate_content(model=name,contents=prompt)
            result=(getattr(response,"text","") or "").strip()
            result=re.sub(r"\n{3,}","\n\n",result).replace("**","").strip()
            if len(result)<80:
                raise RuntimeError(f"Gemini response too short from {name}")
            data["active_gemini_model"]=name
            data["gemini_model_fallbacks"]=[name]+[x for x in candidates if x!=name]
            save(data)
            logging.info("Gemini model success: %s",name)
            return result,name
        except Exception as exc:
            last_error=exc
            logging.warning("Gemini model failed: %s | %s",name,exc)
            if index+1<len(candidates): time.sleep(1)
    data["gemini_model_fallbacks"]=[]
    save(data)
    raise RuntimeError("Configured Gemini models failed. " f"Last error: {last_error}")


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

def _numeric_tokens(text):
    tokens=re.findall(r"(?<!\w)\d+(?:[.,]\d+)*",text or "")
    return {token.replace(",","") for token in tokens}

def _latin_names(text):
    return set(re.findall(r"(?<![A-Za-z])[A-Z][A-Za-z]{2,}(?![A-Za-z])",text or ""))

def validate_generated_post(post,a):
    if not isinstance(post,str) or not post.strip():
        raise ValueError("Gemini returned an empty post")
    lines=[x.strip() for x in post.replace("**","").splitlines() if x.strip()]
    if len(lines)<3:
        raise ValueError("Gemini post has no title, body, and hashtags")
    if len(lines[0])>180:
        raise ValueError("Gemini title is too long")
    hashtag_lines=[line for line in lines[1:] if "#" in line]
    hashtags=re.findall(r"#\S+"," ".join(hashtag_lines))
    if not 3<=len(hashtags)<=6:
        raise ValueError("Gemini post must contain 3 to 6 hashtags")
    source=a.get("text","")
    source_numbers=_numeric_tokens(source)
    output_numbers=_numeric_tokens(post)
    unsupported_numbers=output_numbers-source_numbers
    if unsupported_numbers:
        raise ValueError("Gemini introduced unsupported numbers: "+", ".join(sorted(unsupported_numbers)))
    source_names={name.casefold() for name in _latin_names(source)}
    output_names={name for name in _latin_names(post) if name.casefold() not in {"html"}}
    unsupported_names={name for name in output_names if name.casefold() not in source_names}
    if unsupported_names:
        raise ValueError("Gemini introduced unsupported names: "+", ".join(sorted(unsupported_names)))
    output_urls=set(re.findall(r"https?://\S+",post))
    if output_urls:
        raise ValueError("Gemini introduced an unsupported URL")
    content_before_hashtags="\n".join(line for line in lines if "#" not in line)
    if len(content_before_hashtags)>900:
        raise ValueError("Gemini content is too long")
    return True

def build(post,source_name="",source_url=""):
    lines=[x.strip() for x in post.replace("**","").splitlines() if x.strip()]
    title=lines[0] if lines else "معلومة جديدة تستحق أن تعرفها"
    hs=[x for x in lines[1:] if "#" in x]
    body=[x for x in lines[1:] if "#" not in x]
    tags=re.findall(r"#\S+"," ".join(hs))
    tags=[x for x in tags if x!="#قلعة_المعلومات_العامة"][:6] or ["#علوم","#معرفة","#معلومات_عامة"]
    body="\n\n".join(body) or "تفاصيل هذه المعلومة تكشف جانباً مثيراً من عالم المعرفة، وتفتح أمام القارئ سؤالاً جديداً."
    source_tail=""
    if source_name and isinstance(source_url,str) and source_url.startswith("http"):
        safe_name=html.escape(source_name)
        safe_url=html.escape(source_url,quote=True)
        source_tail=f"\n\nالمصدر: {safe_name}\nالرابط الأصلي: <a href=\"{safe_url}\">{safe_url}</a>"
    tail=source_tail+"\n\n━━━━━━━━━━━━\n\nلأنك تستحق أن تعرف\n\n"+" ".join(tags)+"\n\n"+SIGNATURE
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
    except (requests.RequestException, OSError, UnidentifiedImageError, ValueError) as exc:
        logging.warning("image download/convert failed: url=%s reason=%s",url,exc)
        return None
    finally:
        try:
            raw.unlink(missing_ok=True)
        except OSError as exc:
            logging.warning("raw image cleanup failed: %s",exc)

def telegram(caption,img=None,source_url="",duplicate_checked=False):
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
            except (RPCError, ValueError, TypeError) as username_exc:
                logging.warning(
                    "Unable to resolve Telegram channel by username %s: %s",
                    TELEGRAM_CHANNEL_USERNAME,
                    type(username_exc).__name__,
                )
                try:
                    chat = app.get_chat(int(TELEGRAM_CHANNEL_ID))
                except (RPCError, ValueError, TypeError) as id_exc:
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
            except (RPCError, ValueError, TypeError) as membership_exc:
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

            if source_url and source_url.startswith("http") and not duplicate_checked:
                try:
                    duplicate=next(iter(app.search_messages(chat_id,query=source_url,limit=1)),None)
                except (RPCError, ValueError, TypeError) as exc:
                    raise RuntimeError(
                        "Unable to verify whether the source was already posted."
                    ) from exc
                if duplicate is not None:
                    logging.warning("Source already posted; skipping Telegram send: %s",source_url)
                    return

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
                except (RPCError, OSError, ValueError) as exc:
                    logging.warning(
                        "Photo publish failed, falling back to text: %s",
                        exc
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
            except OSError as exc:
                logging.warning("converted image cleanup failed: %s",exc)

def remember(d,a,caption):
    d.setdefault("posted_links",[]).append(a.get("link",""))
    d.setdefault("posted_titles",[]).append(a.get("title",""))
    d["posted_links"]=d["posted_links"][-700:]; d["posted_titles"]=d["posted_titles"][-700:]
    d.setdefault("last_posts",[]).append({"time":datetime.now(pytz.timezone(TIMEZONE)).isoformat(),"title":a.get("title",""),
        "source":a.get("source",""),"category":a.get("category",""),"link":a.get("link",""),"image":bool(a.get("image_url"))})
    d["last_posts"]=d["last_posts"][-40:]; d["last_error"]=""; save(d)

def telegram_source_exists(source_url):
    if not isinstance(source_url,str) or not source_url.startswith("http"):
        return False
    try:
        with Client("castle_info_dedup",api_id=API_ID,api_hash=API_HASH,bot_token=BOT_TOKEN,in_memory=True) as app:
            duplicate=next(iter(app.search_messages(int(TELEGRAM_CHANNEL_ID),query=source_url,limit=1)),None)
            return duplicate is not None
    except (RPCError, ValueError, TypeError) as exc:
        raise RuntimeError("Unable to verify Telegram source history") from exc

def run():
    d=load()
    try:
        a=candidate(d)
        duplicate_checked=False
        if a and a.get("mode")=="rss" and a.get("link","").startswith("http"):
            if telegram_source_exists(a["link"]):
                logging.warning("Source already posted; recording it without Gemini or Telegram send: %s",a["link"])
                remember(d,a,"")
                return
            duplicate_checked=True
        if a:
            text=generate_with_auto_model(article_prompt(a), d)[0]
        else:
            t=random.choice([x for x in FALLBACK if x not in set(d.get("posted_titles",[]))] or FALLBACK)
            text=generate_with_auto_model(fallback_prompt(t), d)[0]
            a={"mode":"fallback","source":"Gemini","category":"معلومات عامة","title":t,"link":"fallback:"+t,"image_url":None}
        if leak(text): raise RuntimeError("Gemini returned instruction/prompt text")
        validate_generated_post(text,a)
        caption=build(text,a.get("source",""),a.get("link",""))
        telegram(caption,a.get("image_url"),a.get("link",""),duplicate_checked); remember(d,a,caption)
        logging.info("SUCCESS")
    except Exception as e:
        d["last_error"]=f"{datetime.now().isoformat()} | {e}"; save(d); raise

if __name__=="__main__":
    validate()
    run()
