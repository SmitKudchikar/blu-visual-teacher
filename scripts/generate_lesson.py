"""Generate a lesson locally with Ollama; validate it and fall back to the sample on failure."""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator
from validate_lesson import semantic_errors

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:11434/api/chat"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


STOPWORDS = {"a", "an", "the", "is", "are", "am", "me", "my", "to", "for", "in", "on", "of", "and", "or", "how", "what", "why", "teach", "explain", "show", "please", "about", "does", "do", "can", "you", "i"}

NON_CODE_CUES = {"photosynthesis", "water cycle", "cloud", "gravity", "fraction", "circuit", "solar", "password", "kite", "evaporation", "condensation", "precipitation", "पौधे", "पतंग", "पानी", "गुरुत्व", "भिन्न", "परिपथ"}
LANGUAGE_CUES = {
    "en-IN": {"sunlight": "sunlight", "water": "water", "carbon dioxide": "carbon dioxide", "gravity": "gravity", "fraction": "fraction", "cloud": "cloud", "kite": "kite", "password": "password", "photosynthesis": "photosynthesis"},
    "hi-IN": {"sunlight": "सूर्य का प्रकाश", "water": "पानी", "carbon dioxide": "कार्बन डाइऑक्साइड", "gravity": "गुरुत्वाकर्षण", "fraction": "भिन्न", "cloud": "बादल", "kite": "पतंग", "password": "पासवर्ड", "photosynthesis": "प्रकाश संश्लेषण"},
    "ta-IN": {"sunlight": "சூரிய ஒளி", "water": "நீர்", "carbon dioxide": "கார்பன் டை ஆக்சைடு", "gravity": "ஈர்ப்பு விசை", "fraction": "பின்னம்", "cloud": "மேகம்", "kite": "பட்டம்", "password": "கடவுச்சொல்", "photosynthesis": "ஒளிச்சேர்க்கை"},
}


def topic_terms(topic: str) -> list[str]:
    words = [w.casefold() for w in re.findall(r"[^\W_]+", topic, flags=re.UNICODE) if len(w) > 2 and w.casefold() not in STOPWORDS]
    return list(dict.fromkeys(words))


def lesson_topic_terms(topic: str, languages: list[str]) -> list[str]:
    """Compare terms only when the prompt and lesson use the same script."""
    terms = topic_terms(topic)
    language = languages[0] if languages else "en-IN"
    scripts = {"hi-IN": r"[\u0900-\u097f]", "mr-IN": r"[\u0900-\u097f]",
               "ta-IN": r"[\u0b80-\u0bff]", "te-IN": r"[\u0c00-\u0c7f]",
               "bn-IN": r"[\u0980-\u09ff]"}
    if language == "en-IN" or re.search(scripts.get(language, r"(?!)"), topic):
        return terms
    if language == "hi-IN" and any(word in topic.casefold() for word in ("insect", "bug", "pest")):
        return ["कीट", "कीड़े", "पौध", "परागण"]
    if language == "hi-IN" and ("animal" in topic.casefold() or "species" in topic.casefold()):
        return ["प्रजाति", "जानवर", "पशु", "जीव"]
    return []



OBJECT_ALIASES = {
    "sun": ("sun", "sunlight", "solar", "सूर्य", "धूप", "सूरज", "சூரியன்"),
    "moon": ("moon", "चंद्रमा", "चाँद", "நிலா"),
    "cloud": ("cloud", "बादल", "மேகம்"),
    "plant": ("plant", "पौधा", "पौधे", "தாவரம்"),
    "flower": ("flower", "फूल", "மலர்"),
    "water": ("water", "पानी", "जल", "நீர்"),
    "earth": ("earth", "पृथ्वी", "धरती", "பூமி"),
    "ball": ("ball", "गेंद", "பந்து"),
    "insect": ("insect", "कीट", "कीड़े", "பூச்சி"),
}


def add_prompt_objects(lesson: dict, topic: str) -> dict:
    """Carry concrete nouns from the original prompt into both renderers."""
    if not isinstance(lesson, dict):
        return lesson
    text = topic.casefold()
    matches = []
    for name, aliases in OBJECT_ALIASES.items():
        positions = [match.start() for alias in aliases if (match := re.search(r"\b" + re.escape(alias) + r"\b", text))]
        if positions:
            matches.append((min(positions), name))
    lesson["visualObjects"] = [name for _, name in sorted(matches)[:3]]
    return lesson

def is_code_topic(topic: str) -> bool:
    text = topic.casefold()
    programming = ("python", "programming", "array", "arrays", "list", "lists", "loop", "loops", "function", "functions", "recursion", "recursive", "search", "code", "coding", "algorithm", "algorithms", "डेटा", "लूप", "कोड")
    return any(re.search(r"\b" + re.escape(term) + r"\b", text) for term in programming)


def requested_language_error(lesson: dict, languages: list[str]) -> list[str]:
    errors = []
    if lesson.get("languages") != languages:
        errors.append("languages: must exactly match the requested language codes and order")
    for index, scene in enumerate(lesson.get("scenes", [])):
        for field in ("narration", "captions"):
            actual = set(scene.get(field, {})) if isinstance(scene.get(field), dict) else set()
            if actual != set(languages):
                errors.append(f"scenes[{index}].{field}: must contain exactly the requested languages")
            localized = scene.get(field, {})
            if isinstance(localized, dict):
                for lang, value in localized.items():
                    script = {"hi-IN": r"\u0900-\u097f", "mr-IN": r"\u0900-\u097f", "ta-IN": r"\u0b80-\u0bff", "te-IN": r"\u0c00-\u0c7f", "bn-IN": r"\u0980-\u09ff"}.get(lang)
                    if script and not re.search(f"[{script}]", str(value)):
                        errors.append(f"scenes[{index}].{field}.{lang}: text does not appear to use the selected language script")
    for field in ("title", "concept"):
        value = str(lesson.get(field, ""))
        for lang in languages[:1]:
            script = {"hi-IN": r"\u0900-\u097f", "mr-IN": r"\u0900-\u097f", "ta-IN": r"\u0b80-\u0bff", "te-IN": r"\u0c00-\u0c7f", "bn-IN": r"\u0980-\u09ff"}.get(lang)
            if script and not re.search(f"[{script}]", value):
                errors.append(f"{field}: text does not appear to use the selected language script ({lang})")
    return errors


def request_fit_errors(lesson: dict, topic: str, level: str, duration: int, languages: list[str]) -> list[str]:
    errors = requested_language_error(lesson, languages)
    if lesson.get("level") != level:
        errors.append(f"level: expected {level}")
    if lesson.get("targetDurationSeconds") != duration:
        errors.append(f"targetDurationSeconds: expected {duration}")
    all_text = json.dumps(lesson, ensure_ascii=False).casefold()
    terms = lesson_topic_terms(topic, languages)
    matched = [term for term in terms if term in all_text or (term.endswith("s") and term[:-1] in all_text)]
    if terms and len(matched) < max(1, (len(terms) + 3) // 4):
        errors.append("<topic>: lesson text has too little overlap with the requested topic")
    for i, scene in enumerate(lesson.get("scenes", [])):
        scene_text = json.dumps(scene, ensure_ascii=False).casefold()
        if terms and not any(term in scene_text or (term.endswith("s") and term[:-1] in scene_text) for term in terms):
            errors.append(f"scenes[{i}]: no recognizable connection to the requested topic")
    expected = duration * 1.6
    for lang in languages:
        total_words = sum(len(scene.get("narration", {}).get(lang, "").split()) for scene in lesson.get("scenes", []))
        if total_words < expected * .25 or total_words > expected * 1.5:
            errors.append(f"narration.{lang}: {total_words} words is outside the concise duration range ({int(expected * .25)}–{int(expected * 1.5)} words)")
        sentences: set[str] = set()
        for scene_index, scene in enumerate(lesson.get("scenes", [])):
            text = scene.get("narration", {}).get(lang, "")
            for sentence in re.split(r"(?<=[.!?।])\s+", text):
                normalized = re.sub(r"[^\w]+", " ", sentence.casefold(), flags=re.UNICODE).strip()
                if len(normalized.split()) < 5:
                    continue
                if normalized in sentences:
                    errors.append(f"narration.{lang}: repeated sentence detected; each scene must add a new teaching step")
                    break
                sentences.add(normalized)
        for scene_index, scene in enumerate(lesson.get("scenes", [])):
            caption = scene.get("captions", {}).get(lang, "")
            if len(caption) > 100:
                errors.append(f"scenes[{scene_index}].captions.{lang}: caption is too long to fit the video frame")
    return errors


def concise_caption(text: str, max_chars: int = 90) -> str:
    sentence = re.split(r"(?<=[.!?।])\s+", str(text).strip(), maxsplit=1)[0]
    if len(sentence) <= max_chars:
        return sentence
    clipped = sentence[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{clipped}…"


def fallback_lesson(topic: str, level: str, duration: int, languages: list[str]) -> dict:
    terms = topic_terms(topic)
    subject = "moon phases" if re.search(r"\b(?:moon|चंद्रमा|चाँद|நிலா)\b", topic.casefold()) else "sun" if re.search(r"\b(?:sun|सूर्य|सूरज|சூரியன்)\b", topic.casefold()) else "insects on plants" if any(word in topic.casefold() for word in ("insect", "bug", "pest", "कीट", "कीड़े")) else "animal species" if any(word in topic.casefold() for word in ("animal", "species", "प्रजाति", "जानवर")) else next((key for key in ("binary search", "photosynthesis", "water cycle", "cloud", "gravity", "fraction", "circuit", "solar", "password", "kite", "recursion", "array", "loop") if key in topic.casefold()), "the requested idea")
    titles = {"en-IN": f"Understanding {subject}", "hi-IN": f"{subject} को समझना", "ta-IN": f"{subject} புரிந்துகொள்வோம்"}
    gloss = {"en-IN": topic, "hi-IN": f"इस पाठ का विषय: {topic}", "ta-IN": f"இந்தப் பாடத்தின் தலைப்பு: {topic}"}
    title = next((titles.get(lang, topic) for lang in languages if lang in titles), topic)
    concept = next((gloss.get(lang, topic) for lang in languages if lang in gloss), topic)
    def localized(en: str, hi: str, ta: str) -> dict:
        options = {"en-IN": en, "hi-IN": hi, "ta-IN": ta}
        return {lang: options.get(lang, en) for lang in languages}
    teaching = {
        "moon phases": [
            ("The Moon does not change its shape. Sunlight always lights half of it, but from Earth we see different portions of that lit half.", "चंद्रमा अपना आकार नहीं बदलता। सूर्य उसकी आधी सतह को रोशन करता है, पर पृथ्वी से हमें उस रोशन भाग का अलग-अलग हिस्सा दिखता है।", "நிலா வடிவம் மாறாது. சூரிய ஒளி அதன் ஒரு பாதியை எப்போதும் ஒளிரச் செய்கிறது; பூமியிலிருந்து அந்த ஒளிரும் பகுதியின் வெவ்வேறு அளவுகளைக் காண்கிறோம்."),
            ("As the Moon orbits Earth, our viewing angle changes. We see a thin crescent, then a half Moon, and eventually a full Moon.", "चंद्रमा पृथ्वी की परिक्रमा करता है, इसलिए देखने का कोण बदलता है। हमें पहले पतला अर्धचंद्र, फिर आधा और बाद में पूरा चंद्रमा दिखाई देता है।", "நிலா பூமியைச் சுற்றும்போது நாம் பார்க்கும் கோணம் மாறுகிறது. பிறை, அரைநிலா, பின்னர் முழுநிலா எனக் காண்கிறோம்."),
            ("The Moon phase pattern repeats about every 29 and a half days. These regular phases are not caused by Earth's shadow; Earth's shadow causes a lunar eclipse instead.", "यह क्रम लगभग साढ़े 29 दिन में दोहराता है। सामान्य कलाएँ पृथ्वी की छाया से नहीं बनतीं; पृथ्वी की छाया पड़ने पर चंद्र ग्रहण होता है।", "இந்த நிலைகள் சுமார் 29½ நாட்களுக்கு ஒருமுறை மீண்டும் வருகின்றன. வழக்கமான நிலைகள் பூமியின் நிழலால் ஏற்படாது; பூமியின் நிழல் நிலா கிரகணத்தை உண்டாக்கும்."),
        ],
        "sun": [
            ("The Sun is a star at the center of our solar system. Its light and heat reach Earth and support life here.", "सूर्य हमारे सौरमंडल के केंद्र में स्थित एक तारा है। उसका प्रकाश और ताप पृथ्वी तक पहुँचते हैं और जीवन के लिए ऊर्जा देते हैं।", "சூரியன் நமது சூரியக் குடும்பத்தின் மையத்தில் உள்ள ஒரு நட்சத்திரம். அதன் ஒளியும் வெப்பமும் பூமியை அடைந்து உயிர்களுக்கு ஆற்றல் தருகின்றன."),
            ("Sunlight travels through space to Earth. Plants use some of this light to make food, while sunlight also warms land and water.", "सूर्य का प्रकाश अंतरिक्ष से पृथ्वी तक आता है। पौधे इस ऊर्जा से भोजन बनाते हैं, और धूप भूमि तथा पानी को गर्म करती है।", "சூரிய ஒளி விண்வெளி வழியாக பூமிக்கு வருகிறது. தாவரங்கள் இந்த ஒளியால் உணவு தயாரிக்கின்றன; நிலமும் நீரும் வெப்பமடைகின்றன."),
            ("Earth rotates, so the Sun appears to rise and set. The Sun itself keeps shining; our changing view creates day and night.", "पृथ्वी घूमती है, इसलिए हमें सूर्य उगता और डूबता दिखाई देता है। सूर्य चमकता रहता है; पृथ्वी के घूमने से दिन और रात होते हैं।", "பூமி சுழல்வதால் சூரியன் உதித்து மறைவது போலத் தெரிகிறது. சூரியன் தொடர்ந்து ஒளிர்கிறது; பூமியின் சுழற்சியே பகலும் இரவும் உருவாக்குகிறது."),
        ],
        "loop": [
            ("A Python for loop runs the same block once for each item in a sequence. The variable holds the current item, so you do not write the same command repeatedly.", "Python का for loop किसी सूची के हर तत्व के लिए एक ही निर्देश चलाता है। हर चक्र में variable वर्तमान तत्व रखता है, इसलिए वही निर्देश बार-बार लिखना नहीं पड़ता।", "Python for loop ஒரு பட்டியலின் ஒவ்வொரு உருப்படிக்கும் ஒரே கட்டளையை இயக்குகிறது. ஒவ்வொரு சுற்றிலும் மாறி தற்போதைய உருப்படியை வைத்திருக்கும்."),
            ("For the list one, two, three, the loop takes one first and runs its indented body. It then takes two, then three, running the body each time.", "[1, 2, 3] सूची में loop पहले 1 लेता है और अंदर लिखा print चलाता है। फिर 2 और 3 के लिए भी वही काम करता है।", "[1, 2, 3] பட்டியலில் loop முதலில் 1-ஐ எடுத்து உள்தள்ளிய print கட்டளையை இயக்கும். பின்னர் 2, 3-க்கும் அதே செயலைச் செய்யும்."),
            ("The example prints 1, 2, and 3 on separate lines. Once all items are visited, the loop stops. The indented line is the part that repeats.", "इस उदाहरण में 1, 2 और 3 अलग-अलग पंक्तियों में छपते हैं। सभी तत्व पूरे होने पर loop रुक जाता है। अंदर खिसकी हुई पंक्ति ही दोहराई जाती है।", "இந்த எடுத்துக்காட்டு 1, 2, 3-ஐ தனித்தனி வரிகளில் அச்சிடும். எல்லா உருப்படிகளையும் பார்த்ததும் loop நிற்கும். உள்தள்ளிய வரிதான் மீண்டும் இயங்குகிறது."),
        ],
        "insects on plants": [
            ("Insects visit plants for food or shelter. Some chew leaves or drink sap, while others carry pollen between flowers.", "कीट भोजन या आश्रय के लिए पौधों पर आते हैं। कुछ पत्ते खाते या रस चूसते हैं, जबकि कुछ फूलों के बीच पराग ले जाते हैं।", "பூச்சிகள் உணவு அல்லது தங்குமிடத்திற்காகத் தாவரங்களுக்கு வருகின்றன. சில இலைகளைத் தின்னும்; சில மலர்களுக்கு இடையே மகரந்தத்தை எடுத்துச் செல்லும்."),
            ("A caterpillar may leave holes in a leaf. A bee visits flowers and can help pollinate the plant. These insects affect plants in different ways.", "इल्ली पत्ते में छेद कर सकती है। मधुमक्खी फूलों पर जाकर परागण में मदद करती है। इसलिए सभी कीट पौधों को एक जैसा प्रभावित नहीं करते।", "புழு இலையில் துளை ஏற்படுத்தலாம். தேனீ மலர்களுக்குச் சென்று மகரந்தச் சேர்க்கைக்கு உதவும். பூச்சிகளின் விளைவுகள் வேறுபடும்."),
            ("Look for leaf holes, sticky sap, and visiting pollinators. Identify the insect before deciding whether the plant needs protection; useful pollinators should be preserved.", "पत्तों के छेद, चिपचिपा रस और फूलों पर आने वाले कीट देखें। पौधे की रक्षा करने से पहले कीट पहचानें; लाभकारी परागणकर्ताओं को बचाएँ।", "இலைத் துளைகள், ஒட்டும் சாறு, மலருக்கு வரும் பூச்சிகள் ஆகியவற்றைக் கவனியுங்கள். தாவரத்தைப் பாதுகாக்கும் முன் பூச்சியை அடையாளம் காணுங்கள்; பயனுள்ள மகரந்தச் சேர்க்கையாளர்களைக் காப்பாற்றுங்கள்."),
        ],
        "animal species": [
            ("A species is a group of similar animals that can reproduce with each other. It is a way to classify living things by shared traits and ancestry.", "प्रजाति समान गुणों और वंश वाले जीवों का समूह है। एक ही प्रजाति के जानवर सामान्यतः आपस में प्रजनन करके संतान पैदा कर सकते हैं।", "ஒரு இனம் என்பது ஒரே பண்புகளும் வம்சமும் கொண்ட விலங்குகளின் குழு. அவை பொதுவாக தங்களுக்குள் இனப்பெருக்கம் செய்ய முடியும்."),
            ("Compare a lion and a tiger. Both are big cats, but they have different traits and belong to different species.", "शेर और बाघ दोनों बड़ी बिल्लियाँ हैं, पर उनकी विशेषताएँ अलग हैं। इसलिए वे अलग-अलग प्रजातियाँ हैं।", "சிங்கமும் புலியும் பெரிய பூனைகள். ஆனால் அவற்றின் பண்புகள் வேறுபடுவதால் அவை வெவ்வேறு இனங்கள்."),
            ("Animals within one species may look different, yet share important inherited features. Scientists use evidence such as body structure and DNA to identify species.", "एक प्रजाति के जानवर दिखने में कुछ अलग हो सकते हैं, फिर भी उनके वंशानुगत गुण साझा होते हैं। वैज्ञानिक शरीर की बनावट और डीएनए से प्रजाति पहचानते हैं।", "ஒரே இன விலங்குகள் தோற்றத்தில் மாறலாம். உடல் அமைப்பும் மரபணுவும் இனத்தை அறிய உதவுகின்றன."),
        ],
        "binary search": [
            ("Binary search finds a target in a sorted list. Sorting is required because order tells us which side may contain the target.", "Binary search sorted सूची में लक्ष्य खोजता है। क्रम से पता चलता है कि लक्ष्य किस तरफ़ हो सकता है।", "Binary search வரிசைப்படுத்தப்பட்ட பட்டியலில் இலக்கைக் கண்டுபிடிக்கும். வரிசை இலக்கு எந்தப் பக்கத்தில் இருக்கலாம் என்பதைக் காட்டுகிறது."),
            ("Compare the target with the middle item. If the target is larger, keep the right half; if smaller, keep the left half. Discard the other half.", "लक्ष्य की तुलना बीच के item से करें। बड़ा हो तो दायाँ आधा रखें, छोटा हो तो बायाँ आधा। दूसरे आधे को हटा दें।", "இலக்கை நடு உருப்படியுடன் ஒப்பிடுங்கள். பெரியதாக இருந்தால் வலது பாதியையும், சிறியதாக இருந்தால் இடது பாதியையும் வைத்துக்கொண்டு மற்றதை நீக்குங்கள்."),
            ("Repeat on the remaining range. Each comparison roughly halves the candidates, so a long sorted list can be searched in few steps.", "बची हुई सीमा में तुलना दोहराएँ। हर चरण में संभावित मान आधे रह जाते हैं, इसलिए लंबी sorted सूची कुछ चरणों में खोजी जा सकती है।", "மீதமுள்ள வரம்பில் ஒப்பீட்டைத் தொடருங்கள். ஒவ்வொரு படியிலும் சாத்தியமான உருப்படிகள் பாதியாகக் குறைகின்றன; நீண்ட பட்டியலையும் சில படிகளில் தேடலாம்."),
        ],
        "gravity": [
            ("Gravity is an attraction between masses. It acts even when objects are not touching.", "गुरुत्वाकर्षण द्रव्यमान वाली वस्तुओं के बीच आकर्षण है। वस्तुएँ छुए बिना भी यह काम करता है।", "ஈர்ப்பு விசை என்பது நிறையுள்ள பொருட்களுக்கு இடையிலான ஈர்ப்பு. பொருட்கள் தொடாமல் இருந்தாலும் இது செயல்படும்."),
            ("Earth has a very large mass, so it pulls a nearby ball toward its center. The ground stops the ball when it lands.", "पृथ्वी का द्रव्यमान बड़ा है, इसलिए वह गेंद को अपने केंद्र की ओर खींचती है। ज़मीन पर पहुँचकर गेंद रुकती है।", "பூமியின் நிறை பெரியது; அது பந்தைத் தன் மையத்தை நோக்கி இழுக்கிறது. தரையை அடைந்ததும் பந்து நிற்கிறது."),
            ("When you let go of a ball, gravity makes it fall toward Earth. This everyday motion is one visible effect of gravity.", "गेंद को छोड़ने पर गुरुत्वाकर्षण उसे पृथ्वी की ओर गिराता है। यह गुरुत्व का रोज़ दिखने वाला उदाहरण है।", "பந்தைக் கைவிட்டால் ஈர்ப்பு விசை அதை பூமியை நோக்கி விழச் செய்கிறது. இது அன்றாடத்தில் காணும் ஈர்ப்பின் விளைவு."),
        ],
        "fraction": [
            ("Imagine a pizza cut into eight equal slices. The denominator tells how many equal parts make the whole.", "एक पिज़्ज़ा के आठ बराबर टुकड़े सोचें। हर बताता है कि पूरे में कितने बराबर भाग हैं।", "ஒரு பீட்சாவின் எட்டு சம துண்டுகளை நினையுங்கள். கீழெண் முழுதின் சம பாகங்களின் எண்ணிக்கையைக் காட்டுகிறது."),
            ("In 3/8, the denominator is 8: there are eight equal parts. The numerator is 3: three parts are selected.", "3/8 में हर 8 है: कुल आठ बराबर भाग। अंश 3 है: चुने गए तीन भाग।", "3/8 இல் கீழெண் 8: மொத்தம் எட்டு சம பாகங்கள். மேலெண் 3: தேர்ந்தெடுத்த மூன்று பாகங்கள்."),
            ("So 3/8 means three of eight equal parts. The numerator counts selected parts; the denominator counts all equal parts.", "इसलिए 3/8 का अर्थ आठ बराबर भागों में से तीन है। अंश चुने भाग गिनता है और हर सभी भाग।", "3/8 என்பது எட்டு சம பாகங்களில் மூன்று. மேலெண் தேர்ந்தெடுத்த பாகங்களையும் கீழெண் மொத்த பாகங்களையும் குறிக்கும்."),
        ],
    }
    explanations = teaching.get(subject, [
        (f"This lesson introduces {topic}. Focus on the main idea and notice how its parts relate to one another.", f"इस पाठ में {topic} का परिचय है। मुख्य विचार और उसके भागों के संबंध पर ध्यान दें।", f"இந்தப் பாடம் {topic} பற்றி அறிமுகப்படுத்துகிறது. முக்கியக் கருத்தையும் அதன் பகுதிகளின் தொடர்பையும் கவனியுங்கள்."),
        (f"Use the visual to connect the parts of {topic}. Follow the labels and notice how one part relates to the next.", f"दृश्य में {topic} के भाग जोड़कर देखें। लेबल पढ़ें और उनके संबंध समझें।", f"காட்சியில் {topic} இன் பகுதிகளை இணைத்துப் பாருங்கள். குறிச்சொற்களைப் பின்பற்றி அவற்றின் தொடர்பைக் கவனியுங்கள்."),
        (f"Remember the key relationship in {topic}, then use the example in the prompt to explain it in your own words.", f"{topic} का मुख्य संबंध याद रखें और प्रश्न के उदाहरण से उसे अपने शब्दों में समझाएँ।", f"{topic} இன் முக்கியத் தொடர்பை நினைவில் வைத்து, கேள்வியின் எடுத்துக்காட்டால் உங்கள் சொற்களில் விளக்குங்கள்."),
    ])
    scene_types = ["title-card", "diagram", "concept-card"]
    scenes = []
    for idx, kind in enumerate(scene_types):
        visual_text = explanations[idx][{"en-IN": 0, "hi-IN": 1, "ta-IN": 2}.get(languages[0], 0)]
        if kind == "title-card": visual = {"heading": title, "subtitle": topic}
        elif kind == "diagram": visual = {"nodes": [{"id": "topic", "label": topic[:50], "x": .5, "y": .5}], "arrows": []}
        else: visual = {"heading": topic[:55], "points": [visual_text]}
        en, hi, ta = explanations[idx]
        narration = localized(en, hi, ta)
        captions = {lang: concise_caption(narration[lang]) for lang in languages}
        scenes.append({"id": ["introduction", "visual-model", "key-idea"][idx], "type": kind, "narration": narration, "captions": captions, "visual": visual})
    code_examples = {
        "binary search": (["values = [3, 8, 12, 17, 23, 31, 40]", "target = 23", "left, right = 0, len(values) - 1", "middle = (left + right) // 2", "print(values[middle])", "left = middle + 1", "middle = (left + right) // 2", "print(values[middle])", "right = middle - 1", "middle = (left + right) // 2", "print(values[middle])"], ["17", "31", "23"]),
        "array": (["values = [10, 20, 30]", "print(values[1])"], ["20"]),
        "loop": (["for item in [1, 2, 3]:", "    print(item)"], ["1", "2", "3"]),
        "recursion": (["def countdown(n):", "    if n == 0:", "        return", "    print(n)", "    countdown(n - 1)", "countdown(3)"], ["3", "2", "1"]),
    }
    if subject == "moon phases":
        title = {"en-IN": "Why the Moon changes appearance", "hi-IN": "चंद्रमा की कलाएँ", "ta-IN": "நிலாவின் நிலைகள்"}.get(languages[0], title)
        scenes[0]["visual"] = {"heading": title, "subtitle": {"en-IN": "The Moon's shape stays the same", "hi-IN": "आकार वही रहता है", "ta-IN": "வடிவம் மாறாது"}.get(languages[0], topic)}
        scenes[1]["visual"] = {"nodes": [{"id": "sun", "label": {"en-IN": "Sunlight", "hi-IN": "सूर्य का प्रकाश", "ta-IN": "சூரிய ஒளி"}.get(languages[0], "Sunlight"), "x": .18, "y": .5}, {"id": "moon", "label": {"en-IN": "Moon orbits", "hi-IN": "चंद्रमा घूमता है", "ta-IN": "நிலா சுற்றுகிறது"}.get(languages[0], "Moon"), "x": .5, "y": .5}, {"id": "earth", "label": {"en-IN": "View from Earth", "hi-IN": "पृथ्वी से दृश्य", "ta-IN": "பூமியிலிருந்து காட்சி"}.get(languages[0], "Earth"), "x": .82, "y": .5}], "arrows": [{"from": "sun", "to": "moon"}, {"from": "moon", "to": "earth"}]}
        scenes[2]["visual"] = {"heading": {"en-IN": "One repeating cycle", "hi-IN": "दोहराने वाला क्रम", "ta-IN": "மீண்டும் வரும் சுழற்சி"}.get(languages[0], title), "points": [{"en-IN": "Crescent → half → full → crescent", "hi-IN": "अर्धचंद्र → आधा → पूर्णिमा → अर्धचंद्र", "ta-IN": "பிறை → அரைநிலா → முழுநிலா → பிறை"}.get(languages[0], topic)]}
        result_concept = {"en-IN": "Moon phases caused by changing viewing angle", "hi-IN": "देखने का कोण बदलने से चंद्रमा की कलाएँ", "ta-IN": "பார்க்கும் கோணம் மாறுவதால் நிலாவின் நிலைகள்"}.get(languages[0], topic)
    elif subject == "sun":
        title = {"en-IN": "The Sun", "hi-IN": "सूर्य", "ta-IN": "சூரியன்"}.get(languages[0], title)
        scenes[0]["visual"] = {"heading": title, "subtitle": {"en-IN": "Light and heat for Earth", "hi-IN": "पृथ्वी के लिए प्रकाश और ताप", "ta-IN": "பூமிக்கு ஒளியும் வெப்பமும்"}.get(languages[0], topic)}
        scenes[1]["visual"] = {"nodes": [{"id": "sun", "label": title, "x": .25, "y": .5}, {"id": "earth", "label": {"en-IN": "Earth", "hi-IN": "पृथ्वी", "ta-IN": "பூமி"}.get(languages[0], "Earth"), "x": .75, "y": .5}], "arrows": [{"from": "sun", "to": "earth", "label": {"en-IN": "light and heat", "hi-IN": "प्रकाश और ताप", "ta-IN": "ஒளியும் வெப்பமும்"}.get(languages[0], "light")}]}
        scenes[2]["visual"] = {"heading": {"en-IN": "Day and night", "hi-IN": "दिन और रात", "ta-IN": "பகலும் இரவும்"}.get(languages[0], title), "points": [{"en-IN": "Earth rotates while the Sun keeps shining", "hi-IN": "पृथ्वी घूमती है; सूर्य चमकता रहता है", "ta-IN": "பூமி சுழல்கிறது; சூரியன் ஒளிர்கிறது"}.get(languages[0], topic)]}
        result_concept = {"en-IN": "The Sun and its effects on Earth", "hi-IN": "सूर्य और पृथ्वी पर उसका प्रभाव", "ta-IN": "சூரியனும் பூமியில் அதன் விளைவுகளும்"}.get(languages[0], topic)
    elif subject == "loop":
        title = {"en-IN": "How a Python loop works", "hi-IN": "Python loop कैसे काम करता है", "ta-IN": "Python loop எப்படி இயங்குகிறது"}.get(languages[0], title)
        scenes[0]["visual"] = {"heading": title, "subtitle": {"en-IN": "One action for each item", "hi-IN": "हर तत्व पर एक ही क्रिया", "ta-IN": "ஒவ்வொரு உருப்படிக்கும் ஒரே செயல்"}.get(languages[0], topic)}
        scenes[1]["visual"] = {"nodes": [{"id": "list", "label": {"en-IN": "List: 1, 2, 3", "hi-IN": "सूची: 1, 2, 3", "ta-IN": "பட்டியல்: 1, 2, 3"}.get(languages[0], "List"), "x": .2, "y": .5}, {"id": "item", "label": {"en-IN": "Current item", "hi-IN": "वर्तमान तत्व", "ta-IN": "தற்போதைய உருப்படி"}.get(languages[0], "Item"), "x": .5, "y": .5}, {"id": "print", "label": {"en-IN": "Print item", "hi-IN": "तत्व छापें", "ta-IN": "உருப்படியை அச்சிடு"}.get(languages[0], "Print"), "x": .8, "y": .5}], "arrows": [{"from": "list", "to": "item"}, {"from": "item", "to": "print"}]}
        result_concept = {"en-IN": "Python for loop over a list", "hi-IN": "सूची पर Python for loop", "ta-IN": "பட்டியலில் Python for loop"}.get(languages[0], topic)
    elif subject == "insects on plants":
        title = {"en-IN": "Insects on plants", "hi-IN": "पौधों पर कीट", "ta-IN": "தாவரங்களில் பூச்சிகள்"}.get(languages[0], title)
        scenes[0]["visual"] = {"heading": title, "subtitle": {"en-IN": "Pests and pollinators", "hi-IN": "हानिकारक और लाभकारी कीट", "ta-IN": "தீங்கு மற்றும் நன்மை தரும் பூச்சிகள்"}.get(languages[0], topic)}
        scenes[1]["visual"] = {"nodes": [{"id": "insect", "label": {"en-IN": "Insect", "hi-IN": "कीट", "ta-IN": "பூச்சி"}.get(languages[0], "Insect"), "x": .25, "y": .5}, {"id": "plant", "label": {"en-IN": "Plant", "hi-IN": "पौधा", "ta-IN": "தாவரம்"}.get(languages[0], "Plant"), "x": .75, "y": .5}], "arrows": [{"from": "insect", "to": "plant", "label": {"en-IN": "feeding or pollination", "hi-IN": "भोजन या परागण", "ta-IN": "உணவு அல்லது மகரந்தச் சேர்க்கை"}.get(languages[0], "interaction")} ]}
        scenes[2]["visual"] = {"heading": {"en-IN": "Observe before acting", "hi-IN": "पहले पहचानें", "ta-IN": "முதலில் கண்டறியுங்கள்"}.get(languages[0], title), "points": [{"en-IN": "Protect plants while preserving pollinators", "hi-IN": "लाभकारी परागणकर्ताओं को बचाएँ", "ta-IN": "பயனுள்ள மகரந்தச் சேர்க்கையாளர்களைக் காப்பாற்றுங்கள்"}.get(languages[0], topic)]}
        result_concept = {"en-IN": "How insects affect plants", "hi-IN": "कीट पौधों को कैसे प्रभावित करते हैं", "ta-IN": "பூச்சிகள் தாவரங்களை எவ்வாறு பாதிக்கின்றன"}.get(languages[0], topic)
    elif subject == "animal species":
        animal_titles = {"en-IN": "Animal species", "hi-IN": "जानवरों की प्रजातियाँ", "ta-IN": "விலங்கு இனங்கள்"}
        title = animal_titles.get(languages[0], title)
        scenes[0]["visual"] = {"heading": title, "subtitle": {"en-IN": "How scientists group animals", "hi-IN": "जानवरों को कैसे वर्गीकृत करते हैं", "ta-IN": "விலங்குகளை வகைப்படுத்துதல்"}.get(languages[0], topic)}
        scenes[1]["visual"] = {"nodes": [{"id": "group", "label": {"en-IN": "Big cats", "hi-IN": "बड़ी बिल्लियाँ", "ta-IN": "பெரிய பூனைகள்"}.get(languages[0], "Big cats"), "x": .2, "y": .5}, {"id": "species", "label": {"en-IN": "Lion and tiger: different species", "hi-IN": "शेर और बाघ: अलग प्रजातियाँ", "ta-IN": "சிங்கம், புலி: வேறு இனங்கள்"}.get(languages[0], "Different species"), "x": .7, "y": .5}], "arrows": [{"from": "group", "to": "species"}]}
        scenes[2]["visual"] = {"heading": {"en-IN": "How to tell", "hi-IN": "पहचान कैसे करें", "ta-IN": "அடையாளம் காண்பது"}.get(languages[0], title), "points": [{"en-IN": "Compare inherited traits and DNA", "hi-IN": "वंशानुगत गुण और डीएनए देखें", "ta-IN": "மரபுப் பண்புகளையும் டிஎன்ஏவையும் ஒப்பிடுங்கள்"}.get(languages[0], topic)]}
        result_concept = {"en-IN": "Animal species and classification", "hi-IN": "जानवरों की प्रजातियाँ और वर्गीकरण", "ta-IN": "விலங்கு இனங்களும் வகைப்பாடும்"}.get(languages[0], topic)
    else:
        result_concept = concept
    if subject in code_examples:
        code, output = code_examples[subject]
        scenes[2] = {"id": "code-example", "type": "code-walkthrough", "narration": scenes[2]["narration"], "captions": scenes[2]["captions"], "visual": {"heading": localized("Trace the example step by step", "उदाहरण को चरणबद्ध समझें", "எடுத்துக்காட்டைப் படிப்படியாகப் பார்ப்போம்"), "code": code, "highlightLines": list(range(1, len(code) + 1)), "output": output}}
    return {"schemaVersion": "1.0", "id": re.sub(r"[^a-z0-9]+", "-", subject.casefold()).strip("-") or "requested-topic", "title": title, "concept": result_concept, "level": level, "targetDurationSeconds": duration, "languages": languages, "scenes": scenes, "visualObjects": []}


def normalize_visuals(lesson, requested_topic: str = ""):
    if not isinstance(lesson, dict):
        return lesson
    for scene in lesson.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        visual = scene.get("visual") if isinstance(scene.get("visual"), dict) else {}
        kind = scene.get("type")
        if kind == "code-walkthrough" and not is_code_topic(requested_topic):
            kind = "concept-card"
            scene["type"] = kind
        if kind == "diagram":
            raw_nodes = visual.get("nodes", [])
            raw_nodes = raw_nodes if isinstance(raw_nodes, list) else []
            nodes = []
            for index, node in enumerate(raw_nodes[:4]):
                if not isinstance(node, dict):
                    continue
                label = str(node.get("label") or node.get("name") or "").strip()
                if not label:
                    continue
                x = node.get("x", (index + 1) / (len(raw_nodes) + 1))
                y = node.get("y", .5)
                try:
                    x, y = float(x), float(y)
                except (TypeError, ValueError):
                    x, y = (index + 1) / (len(raw_nodes) + 1), .5
                nodes.append({"id": str(node.get("id") or f"node-{index + 1}"), "label": label[:80],
                              "x": max(.15, min(.85, x)), "y": max(.15, min(.85, y))})
            if nodes:
                ids = {node["id"] for node in nodes}
                raw_arrows = visual.get("arrows", [])
                raw_arrows = raw_arrows if isinstance(raw_arrows, list) else []
                arrows = [{"from": str(arrow["from"]), "to": str(arrow["to"]),
                           **({"label": str(arrow["label"])[:60]} if arrow.get("label") else {})}
                          for arrow in raw_arrows if isinstance(arrow, dict) and arrow.get("from") in ids and arrow.get("to") in ids]
                scene["visual"] = {"nodes": nodes, "arrows": arrows}
                continue
            kind = "concept-card"
            scene["type"] = kind
        if kind == "concept-card" or kind not in {"title-card", "code-walkthrough"}:
            scene["type"] = "concept-card"
            heading = visual.get("heading") or visual.get("title") or lesson.get("title") or requested_topic
            if isinstance(heading, dict):
                heading = next((value for value in heading.values() if isinstance(value, str) and value.strip()), requested_topic)
            raw_points = visual.get("points", [])
            if isinstance(raw_points, str):
                raw_points = [raw_points]
            points = [str(point).strip()[:130] for point in raw_points if isinstance(point, str) and point.strip()] if isinstance(raw_points, list) else []
            if not points:
                narration = scene.get("narration", {})
                text = next((value for value in narration.values() if isinstance(value, str) and value.strip()), "") if isinstance(narration, dict) else ""
                points = [concise_caption(text, 100) or str(heading)[:80]]
            scene["visual"] = {"heading": str(heading)[:90], "points": points[:4]}
            continue
        if kind == "code-walkthrough":
            raw_code = visual.get("code", [])
            raw_code = raw_code.splitlines() if isinstance(raw_code, str) else raw_code
            code_lines, inferred_output = [], []
            for raw_line in raw_code if isinstance(raw_code, list) else []:
                line = str(raw_line).strip()
                if not line or line.startswith("```"):
                    continue
                if line.casefold().startswith(("output:", "expected output:")):
                    value = line.split(":", 1)[1].strip().strip("\\\"\\\x27")
                    if value:
                        inferred_output.append(value)
                    continue
                code_lines.append(str(raw_line).rstrip())
            raw_output = visual.get("output", visual.get("expectedOutput", inferred_output))
            if isinstance(raw_output, str):
                outputs = [line.strip() for line in raw_output.splitlines() if line.strip()]
            elif isinstance(raw_output, list):
                outputs = [str(item) for item in raw_output]
            else:
                outputs = inferred_output
            highlights = visual.get("highlightLines", [])
            if not isinstance(highlights, list):
                highlights = []
            highlights = [int(value) for value in highlights if isinstance(value, int) and 1 <= value <= len(code_lines)]
            if not code_lines:
                subject = f"{requested_topic} {lesson.get('id', '')} {lesson.get('title', '')} {lesson.get('concept', '')}".casefold()
                if "array" in subject or "list" in subject:
                    code_lines = ["values = [10, 20, 30]", "print(values[1])"]
                    outputs = ["20"]
                elif "kite" in subject or "पतंग" in subject or "किट" in subject:
                    code_lines = ["wind = 10", "kite_angle = 45", "can_fly = wind > 0 and kite_angle > 0", "print(can_fly)"]
                    outputs = ["True"]
                else:
                    topic_title = str(lesson.get("title", requested_topic or "Lesson"))
                    code_lines = [f"topic = {json.dumps(topic_title, ensure_ascii=False)}", "print(topic)"]
                    outputs = [topic_title]
                highlights = list(range(1, len(code_lines) + 1))
            heading = visual.get("heading")
            defaults = {
                "en-IN": "Trace the example step by step",
                "hi-IN": "उदाहरण को चरणबद्ध समझें",
                "ta-IN": "எடுத்துக்காட்டைப் படிப்படியாகப் பார்ப்போம்",
            }
            languages = lesson.get("languages", ["en-IN"])
            if isinstance(heading, dict):
                localized_heading = {lang: str(heading.get(lang) or defaults.get(lang) or heading.get("en-IN") or "Trace the example") for lang in languages}
            elif isinstance(heading, str) and heading.strip():
                localized_heading = {lang: heading.strip() for lang in languages}
            else:
                localized_heading = {lang: defaults.get(lang, "Trace the example step by step") for lang in languages}
            scene["visual"] = {"heading": localized_heading, "code": code_lines,
                               "highlightLines": highlights or ([1] if code_lines else []), "output": outputs}
            continue
        if scene.get("type") == "diagram":
            nodes = visual.get("nodes", [])
            arrows = visual.get("arrows", [])
            scene["visual"] = {
                "nodes": [node for node in nodes if isinstance(node, dict) and all(key in node for key in ("id", "label", "x", "y"))],
                "arrows": [arrow for arrow in arrows if isinstance(arrow, dict) and all(key in arrow for key in ("from", "to"))],
            }
            continue
        if scene.get("type") == "concept-card":
            heading = visual.get("heading") or lesson.get("title", requested_topic or "Key idea")
            points = visual.get("points", [])
            scene["visual"] = {"heading": str(heading), "points": [str(point) for point in points if str(point).strip()][:4] or [str(heading)]}
            continue
        if scene.get("type") != "title-card":
            continue
        nodes = visual.get("nodes", [])
        node_labels = [node.get("label", "") for node in nodes if isinstance(node, dict)]
        narration = scene.get("narration", {})
        narration_text = next((value for value in narration.values() if isinstance(value, str) and value.strip()), "") if isinstance(narration, dict) else ""
        heading = visual.get("heading") or visual.get("title") or (node_labels[0] if node_labels else lesson.get("title", "Lesson"))
        subtitle = visual.get("subtitle") or (node_labels[1] if len(node_labels) > 1 else "") or narration_text or lesson.get("concept", "")
        scene["visual"] = {"heading": str(heading), "subtitle": str(subtitle)}
    return lesson


CODE_TOPIC_ALIASES = {
    "array": {"array", "arrays", "list", "lists", "values", "items", "elements", "index", "indices", "सूची", "तत्व"},
    "cloud": {"cloud", "clouds", "water", "vapor", "vapour", "condensation", "humidity", "rain", "बादल", "जलवाष्प", "पानी"},
    "kite": {"kite", "kites", "wind", "lift", "air", "angle", "string", "पतंग", "हवा"},
    "photosynthesis": {"photosynthesis", "chlorophyll", "plant", "plants", "sunlight", "carbon", "dioxide", "glucose", "leaf", "leaves", "प्रकाश", "क्लोरोफिल", "पौधे"},
    "binary search": {"binary", "search", "middle", "mid", "sorted"},
    "recursion": {"recursion", "recursive", "countdown", "basecase"},
}
CODE_STOPWORDS = STOPWORDS | {"lesson", "learn", "learning", "beginner", "intermediate", "programming", "program", "concept", "explain", "example", "simple", "basic", "basics", "fundamental", "structure", "data", "show", "understanding", "explained", "explanation", "use", "using", "make", "made", "works", "work", "process"}


def code_topic_error(lesson: dict, requested_topic: str) -> str | None:
    scene = next((item for item in lesson.get("scenes", []) if item.get("type") == "code-walkthrough"), None)
    if not scene:
        return None
    if not is_code_topic(requested_topic):
        return "this is not a programming topic; use a diagram or concept-card instead of a code-walkthrough"
    visual = scene.get("visual", {})
    code = visual.get("code", []) if isinstance(visual, dict) else []
    code_text = "\n".join(code).casefold() if isinstance(code, list) else str(code).casefold()
    if not code_text.strip():
        return "code-walkthrough is empty; add a code example for the requested topic"
    try:
        tree = ast.parse(code_text)
    except SyntaxError:
        return "code-walkthrough is not valid Python; repair the code example"
    allowed_nodes = (ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.For, ast.If, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.IfExp, ast.Name, ast.Load, ast.Store,
                     ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Subscript, ast.Slice, ast.BinOp, ast.UnaryOp,
                     ast.BoolOp, ast.Compare, ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                     ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
                     ast.Gt, ast.GtE, ast.In, ast.NotIn)
    if len(code_lines := code_text.splitlines()) > 20 or any(not isinstance(node, allowed_nodes) for node in ast.walk(tree)):
        return "code-walkthrough uses unsupported or overly long Python; use a small built-in-only example"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in {"print", "range", "len", "sum", "min", "max", "abs"} | {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}):
            return "code-walkthrough calls unsupported Python; use only print and basic built-ins"
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return "code-walkthrough uses a restricted Python name"
    class SafeRange:
        def __call__(self, *args):
            result = range(*args)
            if len(result) > 1000:
                raise ValueError("range exceeds 1000 iterations")
            return result
    namespace = {"__builtins__": {"print": print, "range": SafeRange(), "len": len, "sum": sum, "min": min, "max": max, "abs": abs}}
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            exec(compile(tree, "<lesson-code>", "exec"), namespace, namespace)
    except Exception as exc:
        return f"code-walkthrough cannot run safely: {type(exc).__name__}: {exc}"
    expected = visual.get("output", visual.get("expectedOutput", []))
    if not expected or not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print" for node in ast.walk(tree)):
        return "code-walkthrough must print at least one meaningful result in its expected output"
    if isinstance(expected, str):
        expected = [line for line in expected.splitlines() if line]
    actual = captured.getvalue().splitlines()
    if [str(x).strip() for x in expected] != [line.strip() for line in actual]:
        return f"code-walkthrough output does not match its expected output (expected {expected!r}, got {actual!r})"
    if re.search(r"^\s*topic\s*=.*\n\s*print\(topic\)", code_text):
        return "code-walkthrough is a generic print placeholder, not an example of the requested topic"

    topic_text = f"{requested_topic} {lesson.get('title', '')} {lesson.get('concept', '')}".casefold()
    topic_words = [word for word in re.findall(r"[^\W_]+", topic_text, flags=re.UNICODE) if len(word) > 2 and word not in CODE_STOPWORDS]
    code_for_match = re.sub(r"\"[^\"]*\"|'[^']*'", " ", code_text)
    code_for_match = re.sub(r"#.*", " ", code_for_match)
    code_words = re.findall(r"[^\W_]+", code_for_match, flags=re.UNICODE)
    code_words = {word[:-1] if word.endswith("s") and len(word) > 4 else word for word in code_words}
    if any(word in code_words or (word.endswith("s") and word[:-1] in code_words) for word in topic_words):
        return None

    for subject, aliases in CODE_TOPIC_ALIASES.items():
        if subject in topic_text:
            if subject == "binary search":
                if re.search(r"\b(binary|search)\b", code_for_match) or (re.search(r"\b(mid|middle)\b", code_for_match) and re.search(r"\b(left|right|low|high)\b", code_for_match)):
                    return None
            elif subject == "recursion":
                if re.search(r"\b(countdown|recursive|recursion)\b", code_for_match):
                    return None
            elif any(alias in code_for_match for alias in aliases):
                return None
    if re.search(r"\b(loop|iteration|repeat|repetition|दोहराव)\b", topic_text) and re.search(r"\b(for|while|range|loop)\b", code_text):
        return None
    return "code-walkthrough does not demonstrate the requested topic; replace the generic example with topic-specific code"


def validate_candidate(lesson, schema, requested_topic: str) -> list[str]:
    errors = []
    for error in Draft202012Validator(schema).iter_errors(lesson):
        location = ".".join(map(str, error.absolute_path)) or "<root>"
        message = "does not match the required scene visual structure" if error.validator == "oneOf" else error.message
        errors.append(f"{location}: {message}")
    if isinstance(lesson, dict):
        errors.extend(semantic_errors(lesson))
        code_error = code_topic_error(lesson, requested_topic)
        if code_error:
            errors.append("<topic-code>: " + code_error)
        terms = lesson_topic_terms(requested_topic, lesson.get("languages", []))
        if terms:
            lesson_text = json.dumps(lesson, ensure_ascii=False).casefold()
            matched = sum(1 for word in terms if word in lesson_text or (word.endswith("s") and word[:-1] in lesson_text))
            required = max(1, (len(terms) + 2) // 3)
            if matched < required:
                errors.append("<topic>: generated lesson does not mention enough of the requested topic")
    else:
        errors.append("<root>: model output must be a JSON object")
    return errors


def topic_code_template(lesson: dict, requested_topic: str) -> dict | None:
    subject = f"{requested_topic} {lesson.get('id', '')} {lesson.get('title', '')} {lesson.get('concept', '')}".casefold()
    if any(word in subject for word in ("array", "arrays", "list", "सूची")):
        return {"code": ["my_array = [10, 20, 30]", "print(my_array[1])"], "highlightLines": [1, 2], "output": ["20"]}
    if any(word in subject for word in ("kite", "kites", "पतंग", "किट")):
        return {"code": ["wind_speed = 10", "kite_angle = 45", "lift = wind_speed > 0 and kite_angle > 0", "print(lift)"], "highlightLines": [1, 2, 3, 4], "output": ["True"]}
    if any(word in subject for word in ("cloud", "clouds", "बादल")):
        return {"code": ["air_cools = True", "water_vapor_present = True", "cloud_forms = air_cools and water_vapor_present", "print(cloud_forms)"], "highlightLines": [1, 2, 3, 4], "output": ["True"]}
    if any(word in subject for word in ("loop", "iteration", "repeat", "दोहराव")):
        return {"code": ["for item in [1, 2, 3]:", "    print(item)"], "highlightLines": [1, 2], "output": ["1", "2", "3"]}
    return None


def repair_code_visual(lesson: dict, requested_topic: str, model: str, languages: list[str]) -> dict:
    scene = next(item for item in lesson.get("scenes", []) if item.get("type") == "code-walkthrough")
    code_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "highlightLines", "output"],
        "properties": {
            "code": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "highlightLines": {"type": "array", "items": {"type": "integer", "minimum": 1}},
            "output": {"type": "array", "items": {"type": "string"}},
        },
    }
    narration = scene.get("narration", {})
    explanation = " ".join(value for value in narration.values() if isinstance(value, str)) if isinstance(narration, dict) else ""
    lesson_context = f"Title: {lesson.get('title', '')}\nConcept: {lesson.get('concept', '')}\nExplanation: {explanation}"
    prompt = f"Requested topic: {requested_topic}\nNarration language codes: {', '.join(languages)} (keep the code in Python)\n{lesson_context}\nCreate 2–6 lines of valid Python that directly demonstrate this concept. Use only built-in Python features; do not invent imports or APIs. Do not use a generic loop unless the topic needs iteration. Include print statements whose output agrees with the output array. Return only JSON."
    last_error = "no valid code response"
    for response_format in (code_schema, "json"):
        payload = {
            "model": model,
            "stream": False,
            "format": response_format,
            "options": {"temperature": 0.1, "num_predict": 700, "num_ctx": 8192},
            "messages": [
                {"role": "system", "content": "Generate a topic-specific Python code visual only. Do not translate code into prose. Return JSON with code, highlightLines, and output."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            request = Request(API, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
            visual = json.loads(result.get("message", {}).get("content", ""))
            if not isinstance(visual, dict):
                raise ValueError("code response was not an object")
            scene["visual"] = visual
            lesson = normalize_visuals(lesson, requested_topic)
            last_error = code_topic_error(lesson, requested_topic) or ""
            if not last_error:
                return lesson
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    visual = topic_code_template(lesson, requested_topic)
    if visual:
        scene["visual"] = visual
        lesson = normalize_visuals(lesson, requested_topic)
        last_error = code_topic_error(lesson, requested_topic) or ""
        if not last_error:
            return lesson
    raise ValueError(f"Could not create a valid, topic-specific Python example: {last_error}")


def polish_candidate(lesson: dict, topic: str, languages: list[str]) -> dict:
    """Fix presentation issues without inventing new teaching content."""
    if not isinstance(lesson, dict) or not isinstance(lesson.get("scenes"), list):
        return lesson
    script_patterns = {"hi-IN": r"[\u0900-\u097f]", "ta-IN": r"[\u0b80-\u0bff]",
                       "te-IN": r"[\u0c00-\u0c7f]", "bn-IN": r"[\u0980-\u09ff]",
                       "mr-IN": r"[\u0900-\u097f]"}
    for lang in languages:
        seen: set[str] = set()
        for scene in lesson["scenes"]:
            if not isinstance(scene, dict):
                continue
            narration = scene.get("narration", {})
            if not isinstance(narration, dict) or not isinstance(narration.get(lang), str):
                continue
            original = narration[lang].strip()
            sentences = re.split(r"(?<=[.!?।])\s+", original)
            unique = []
            for sentence in sentences:
                key = re.sub(r"[^\w]+", " ", sentence.casefold(), flags=re.UNICODE).strip()
                if key and key not in seen:
                    unique.append(sentence)
                    if len(key.split()) >= 5:
                        seen.add(key)
            if unique:
                # Cap each scene to two short sentences. Keep the point of the
                # scene visible instead of letting a small model emit essays.
                narration[lang] = " ".join(unique[:2])
            captions = scene.get("captions", {})
            if isinstance(captions, dict):
                captions[lang] = concise_caption(narration[lang])
        pattern = script_patterns.get(lang)
        if pattern and languages[0] == lang:
            localized = [str(scene.get("captions", {}).get(lang, "") or scene.get("narration", {}).get(lang, ""))
                         for scene in lesson["scenes"] if isinstance(scene, dict)]
            localized = [value for value in localized if re.search(pattern, value)]
            if localized:
                if not re.search(pattern, str(lesson.get("title", ""))):
                    titles = {"hi-IN": {"insect": "पौधों पर कीट", "animal": "जानवरों की प्रजातियाँ"}}
                    subject = topic.casefold()
                    mapped = next((label for cue, label in titles.get(lang, {}).items() if cue in subject), None)
                    lesson["title"] = mapped or concise_caption(localized[0], 55)
                if not re.search(pattern, str(lesson.get("concept", ""))):
                    narration = next((str(scene.get("narration", {}).get(lang, "")) for scene in lesson["scenes"]
                                      if isinstance(scene, dict) and re.search(pattern, str(scene.get("narration", {}).get(lang, "")))), localized[0])
                    lesson["concept"] = concise_caption(narration, 110)
                for scene in lesson["scenes"]:
                    if not isinstance(scene, dict) or scene.get("type") != "title-card":
                        continue
                    visual = scene.get("visual", {})
                    if isinstance(visual, dict):
                        if not re.search(pattern, str(visual.get("heading", ""))):
                            visual["heading"] = lesson["title"]
                        if not re.search(pattern, str(visual.get("subtitle", ""))):
                            visual["subtitle"] = lesson["concept"]
    return lesson


def has_specific_fallback(topic: str) -> bool:
    text = topic.casefold()
    return bool(re.search(r"\b(?:sun|सूर्य|सूरज|சூரியன்|moon|चंद्रमा|चाँद|நிலா)\b", text)) or any(cue in text for cue in ("binary search", "gravity", "fraction", "animal", "species",
                                       "insect", "bug", "pest", "कीट", "कीड़े", "loop", "लूप"))

def main() -> int:
    parser = argparse.ArgumentParser(description="Plan a visual lesson with a local Ollama model")
    parser.add_argument("concept", help="lesson topic, for example: How does a Python loop work?")
    parser.add_argument("--level", choices=("beginner", "intermediate"), default="beginner")
    parser.add_argument("--duration", type=int, choices=range(30, 91), default=45)
    parser.add_argument("--languages", default="en-IN,hi-IN,ta-IN", help="comma-separated locale codes")
    parser.add_argument("--model", default="qwen2.5:3b", help="local model name shown by ollama list")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "generated-lesson.json")
    args = parser.parse_args()

    schema_path = ROOT / "schemas" / "lesson.schema.json"
    sample_path = ROOT / "data" / "lessons" / "sample-lesson.json"
    try:
        schema = load(schema_path)
        load(sample_path)  # Keep startup checks for the packaged reference lesson.
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot load schema or sample fallback: {exc}", file=sys.stderr)
        return 2

    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    allowed_languages = {"en-IN", "hi-IN", "ta-IN", "te-IN", "bn-IN", "mr-IN"}
    if not languages or len(languages) != len(set(languages)) or set(languages) - allowed_languages:
        parser.error("--languages must contain unique supported locale codes")
    target_narration_words = round(args.duration * 1.6)
    heading_example = json.dumps({lang: "..." for lang in languages}, ensure_ascii=False)
    prompt_objects = add_prompt_objects({}, args.concept)["visualObjects"]
    prompt = f"""Create an educational visual lesson as JSON data only.
Topic: {args.concept}
Concrete objects to show: {", ".join(prompt_objects) or "none detected"}
Level: {args.level}
Target duration: {args.duration} seconds. Aim for about {target_narration_words} spoken words PER LANGUAGE (about 1.6 words per second); concise delivery is preferred over padding.
Languages: {', '.join(languages)}. Produce a complete equivalent version for every locale.

Requirements:
- Return the lesson object itself matching the supplied JSON schema. Do not wrap it in another object. No markdown or prose outside JSON.
- Make exactly 3 scenes in a teaching sequence: title-card, one visual model, and one concrete example or takeaway. Each scene teaches a different step. Use code-walkthrough only for programming/computational topics; never force code into other subjects.
- Keep narration concise: exactly 1–2 short sentences per scene, roughly {target_narration_words} words total per language. No repeated ideas, filler, or long paragraphs. Captions must be a short takeaway of at most 12 words; visual labels must fit comfortably in their boxes. Translate to each requested language at level {args.level}.
- Use exact visual shapes: title-card: {{"heading":"...","subtitle":"..."}}; diagram: {{"nodes":[{{"id":"a","label":"...","x":0.2,"y":0.5}},{{"id":"b","label":"...","x":0.8,"y":0.5}}],"arrows":[{{"from":"a","to":"b","label":"..."}}]}}; code-walkthrough: {{"heading":{heading_example},"code":["..."],"highlightLines":[1],"output":["..."]}}; concept-card: {{"heading":"...","points":["..."]}}. Localized code-scene headings must be supplied for every requested language.
- Use code only when the topic is computational. Code must be syntactically valid Python, directly relevant, and have a truthful expected output. Use built-ins and no invented APIs. Do not use generic print placeholders or unrelated loops. Put only source-code lines in visual.code.
- If concrete objects are listed above, show those objects in the lesson and give them meaningful roles in the visual explanation.
- Keep visual data simple, clear, and suitable for a 16:9 educational video.
- Set schemaVersion to "1.0", use the requested level and targetDurationSeconds, and include the requested languages.
"""
    model_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schemaVersion", "id", "title", "concept", "level", "targetDurationSeconds", "languages", "scenes"],
        "properties": {
            "schemaVersion": {"const": "1.0"},
            "id": {"type": "string"},
            "title": {"type": "string"},
            "concept": {"type": "string"},
            "level": {"enum": ["beginner", "intermediate"]},
            "targetDurationSeconds": {"type": "integer", "minimum": 30, "maximum": 90},
            "languages": {"type": "array", "minItems": 1, "items": {"enum": ["en-IN", "hi-IN", "ta-IN", "te-IN", "bn-IN", "mr-IN"]}},
            "scenes": {
                "type": "array",
                "minItems": 3,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "type", "narration", "captions", "visual"],
                    "properties": {
                        "id": {"type": "string"},
            "type": {"enum": ["title-card", "diagram", "code-walkthrough", "concept-card"]},
                        "narration": {"type": "object", "additionalProperties": {"type": "string"}},
                        "captions": {"type": "object", "additionalProperties": {"type": "string"}},
                        "visual": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "heading": {"oneOf": [{"type": "string"}, {"type": "object", "required": languages, "additionalProperties": False, "properties": {lang: {"type": "string", "minLength": 1} for lang in languages}}]},
                                "subtitle": {"type": "string"},
                                "nodes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "label", "x", "y"], "properties": {"id": {"type": "string"}, "label": {"type": "string"}, "x": {"type": "number", "minimum": 0, "maximum": 1}, "y": {"type": "number", "minimum": 0, "maximum": 1}}}},
                                "arrows": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["from", "to"], "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "label": {"type": "string"}}}},
                                "code": {"type": "array", "items": {"type": "string"}},
                                "highlightLines": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                                "output": {"type": "array", "items": {"type": "string"}},
                                "points": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        },
    }

    payload = {
        "model": args.model,
        "stream": False,
        "format": model_schema,
        "options": {"temperature": 0.2, "num_predict": 1200, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": "You create accurate beginner-friendly lessons. Output only one JSON object that follows the supplied schema."},
            {"role": "user", "content": prompt},
        ],
    }

    lesson = None
    failure = None
    try:
        request = Request(API, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "")
        try:
            lesson = polish_candidate(normalize_visuals(json.loads(content), args.concept), args.concept, languages)
        except json.JSONDecodeError as parse_error:
            if has_specific_fallback(args.concept):
                raise
            retry_payload = {
                **payload,
                "options": {"temperature": 0.1, "num_predict": 1500, "num_ctx": 4096},
                "messages": [
                    {"role": "system", "content": "Generate one complete, valid lesson JSON object. Do not truncate or wrap it."},
                    {"role": "user", "content": f"The previous response was incomplete JSON ({parse_error}). Regenerate from scratch. Follow these requirements exactly:\n{prompt}"},
                ],
            }
            retry_request = Request(API, data=json.dumps(retry_payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(retry_request, timeout=90) as response:
                retry_result = json.loads(response.read().decode("utf-8"))
            retry_content = retry_result.get("message", {}).get("content", "")
            try:
                lesson = polish_candidate(normalize_visuals(json.loads(retry_content), args.concept), args.concept, languages)
            except json.JSONDecodeError as retry_error:
                stop_reason = retry_result.get("done_reason", "unknown")
                raise json.JSONDecodeError(
                    f"invalid after retry (first response: {parse_error}; retry: {retry_error}; response characters: {len(retry_content)}; stop reason: {stop_reason})",
                    retry_content,
                    retry_error.pos,
                ) from retry_error
        errors = validate_candidate(lesson, schema, args.concept)
        errors.extend(request_fit_errors(lesson, args.concept, args.level, args.duration, languages))
        if errors and has_specific_fallback(args.concept):
            failure = "model lesson failed validation: " + "; ".join(errors[:8])
            lesson = None
            errors = []
        if errors and is_code_topic(args.concept) and all(error.startswith("<topic-code>:") for error in errors):
            try:
                lesson = repair_code_visual(lesson, args.concept, args.model, languages)
                errors = validate_candidate(lesson, schema, args.concept)
                errors.extend(request_fit_errors(lesson, args.concept, args.level, args.duration, languages))
                if errors:
                    failure = "model code lesson failed validation: " + "; ".join(errors[:8])
                    lesson = None
                    errors = []
            except Exception as exc:
                failure = f"model code repair failed: {type(exc).__name__}: {exc}"
                lesson = None
                errors = []
        if errors:
            repair_payload = {
                **payload,
                "options": {"temperature": 0.1, "num_predict": 1300, "num_ctx": 4096},
                "messages": [
                    {"role": "system", "content": "Repair the lesson JSON so it matches the schema and stays strictly about the requested topic. Return only the corrected lesson object."},
                    {"role": "user", "content": f"Requested topic: {args.concept}\nLevel: {args.level}\nLanguage codes: {', '.join(languages)}\nValidation errors: {errors[:8]}\nUse title-card visual keys heading and subtitle; diagram visual keys nodes and arrows; code-walkthrough visual keys localized heading, code, highlightLines, and output. The code-scene heading must map every requested language code to a short translated heading. Use code-walkthrough only for programming topics. For animal, biology, and other non-programming topics use diagrams or concept-cards. If code is appropriate, it must be valid Python and relevant. Keep every scene about the requested topic. Fix this lesson JSON:\n{json.dumps(lesson, ensure_ascii=False)}"},
                ],
            }
            try:
                repair_request = Request(API, data=json.dumps(repair_payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(repair_request, timeout=90) as response:
                    repair_result = json.loads(response.read().decode("utf-8"))
                repaired_lesson = polish_candidate(normalize_visuals(json.loads(repair_result.get("message", {}).get("content", "")), args.concept), args.concept, languages)
                repair_errors = validate_candidate(repaired_lesson, schema, args.concept)
                repair_errors.extend(request_fit_errors(repaired_lesson, args.concept, args.level, args.duration, languages))
                if is_code_topic(args.concept) and any(error.startswith("<topic-code>:") for error in repair_errors):
                    repaired_lesson = repair_code_visual(repaired_lesson, args.concept, args.model, languages)
                    repair_errors = validate_candidate(repaired_lesson, schema, args.concept)
                    repair_errors.extend(request_fit_errors(repaired_lesson, args.concept, args.level, args.duration, languages))
                if repair_errors:
                    failure = "model lesson failed validation after repair: " + "; ".join(repair_errors[:8])
                    lesson = None
                else:
                    lesson = repaired_lesson
            except Exception as exc:
                failure = "model lesson validation repair failed: " + "; ".join(errors[:5]) + f"; {type(exc).__name__}: {exc}"
                lesson = None
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        failure = f"Ollama returned HTTP {exc.code}: {detail[:500]}"
    except (URLError, TimeoutError) as exc:
        failure = f"Could not reach local Ollama at {API}: {exc}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        failure = f"Ollama returned invalid or incomplete JSON: {exc}"
    except Exception as exc:
        failure = f"Local planning failed: {type(exc).__name__}: {exc}"

    used_fallback = lesson is None
    if used_fallback:
        if not has_specific_fallback(args.concept):
            print(f"Could not produce a topic-specific lesson for {args.concept}: {failure}", file=sys.stderr)
            return 3
        lesson = fallback_lesson(args.concept, args.level, args.duration, languages)
        fallback_errors = validate_candidate(lesson, schema, args.concept)
        fallback_errors.extend(request_fit_errors(lesson, args.concept, args.level, args.duration, languages))
        if fallback_errors:
            print(f"Could not build a relevant fallback lesson: {'; '.join(fallback_errors[:8])}", file=sys.stderr)
            return 3
        print(f"Planner fallback: {failure}", file=sys.stderr)
        print(f"Using topic-specific fallback lesson: {lesson['id']}")
    else:
        print(f"Ollama lesson validated: {lesson['id']}")

    lesson = add_prompt_objects(lesson, args.concept)
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(lesson, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Could not write lesson output: {exc}", file=sys.stderr)
        return 2
    print(f"Lesson JSON saved to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
