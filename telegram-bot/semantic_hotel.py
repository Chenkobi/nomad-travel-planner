"""Provider-independent semantic hotel reservation understanding."""
from datetime import datetime
from html import unescape
import re

MONTHS={"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12,"jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12,"ינואר":1,"פברואר":2,"מרץ":3,"אפריל":4,"מאי":5,"יוני":6,"יולי":7,"אוגוסט":8,"ספטמבר":9,"אוקטובר":10,"נובמבר":11,"דצמבר":12}
LABELS={"hotel":("hotel","property","accommodation","מלון","שם המלון"),"city":("city","destination","location","עיר","יעד"),"check_in":("check-in","check in","arrival","arrival date","צ'ק-אין","צ׳ק-אין","הגעה"),"check_out":("check-out","check out","departure","departure date","צ'ק-אאוט","צ׳ק-אאוט","עזיבה")}

def _plain(s):
    s=re.sub(r"</?(?:br|div|p|li|tr|h[1-6]|td)[^>]*>","\n",s or "",flags=re.I); s=re.sub(r"<[^>]+>"," ",s); s=unescape(s).replace("\xa0"," ")
    return "\n".join(re.sub(r"\s+"," ",x).strip() for x in s.splitlines() if x.strip())

def _date(s):
    s=re.sub(r"\s+"," ",(s or "").strip().replace(",",""))
    m=re.fullmatch(r"(\d{1,2})[/. -](\d{1,2})[/. -](20\d{2})",s)
    if m:
        a,b,y=map(int,m.groups())
        if a<=12 and b<=12:return None,.35
        d,mo=(a,b) if b<=12 else (b,a)
        try:return datetime(y,mo,d).date().isoformat(),.88
        except ValueError:return None,0
    for pat,swap in ((r"([A-Za-zÀ-ת]+)\s+(\d{1,2})\s+(20\d{2})",False),(r"(\d{1,2})\s+([A-Za-zÀ-ת]+)\s+(20\d{2})",True)):
        m=re.fullmatch(pat,s,re.I)
        if m:
            a,b,y=m.groups(); day,month=(b,a) if not swap else (a,b); mo=MONTHS.get(month.casefold()) or MONTHS.get(month.casefold().removeprefix("ב"))
            if mo:
                try:return datetime(int(y),mo,int(day)).date().isoformat(),.98
                except ValueError:pass
    try:return datetime.strptime(s,"%Y-%m-%d").date().isoformat(),.98
    except ValueError:return None,0

def _find(field,lines):
    pat=re.compile(r"^\s*(?:"+"|".join(re.escape(x) for x in LABELS[field])+r")\s*[:：-]\s*(.*?)\s*$",re.I)
    for line in lines:
        m=pat.match(line)
        if m:return m.group(1).strip(),line
    return None,None

def _fact(value=None,evidence=None,confidence=0.0):return {"value":value,"evidence":evidence,"confidence":round(confidence,2)}

def understand_hotel_reservation(subject="",body=""):
    subject=re.sub(r"\s+"," ",str(subject or "")).strip(); lines=[x for x in _plain(str(body or "")).splitlines() if x]; combined=(subject+"\n"+"\n".join(lines)).casefold()
    strong=bool(re.search(r"\b(?:reservation|booking|confirmation|confirmed|itinerary|voucher)\b|הזמנה|אישור",combined)); rec=bool(re.search(r"\b(?:recommended|recommendations?|discover|explore|inspiration|guide|suggested)\b|המלצות",combined)); policy=bool(re.search(r"\b(?:policy|policies|terms and conditions|cancellation policy|privacy policy)\b|מדיניות|תנאים",combined)); no=bool(re.search(r"\b(?:no booking|not been booked|not a reservation)\b|לא בוצעה הזמנה",combined))
    facts={}; raw,line=_find("hotel",lines); facts["hotel"]=_fact(raw or None,line,.98 if raw else 0); raw,line=_find("city",lines); facts["city"]=_fact(raw or None,line,.95 if raw else 0)
    if not facts["hotel"]["value"]:
        source = subject + "\n" + "\n".join(lines)
        lowered_source = source.casefold()
        for marker in ("booking is confirmed at ", "your booking is confirmed at ", "reservation is confirmed at ", "booking confirmed at "):
            position = lowered_source.find(marker)
            if position >= 0:
                value = source[position + len(marker):].split(".", 1)[0].split(",", 1)[0].strip()
                if value:
                    facts["hotel"] = _fact(value, source[position:position + len(marker) + len(value)], .93)
                    break
    if not facts["hotel"]["value"]:
        match=re.search(r"\b(?:reservation|booking)\s+at\s+(.+?)(?=\s+is\s+(?:confirmed|booked)|[,.]\s|$)", subject+"\n"+"\n".join(lines), re.I)
        if match:
            facts["hotel"]=_fact(match.group(1).strip(), match.group(0), .93)
    for f in ("check_in","check_out"):
        raw,line=_find(f,lines)
        if not raw:
            words=("arrive|arrival|check[- ]?in") if f=="check_in" else ("depart|departure|check[- ]?out")
            match=re.search(rf"\b(?:{words})\s+(?:on\s+)?((?:[A-Za-zÀ-ת]+\s+\d{{1,2}}\s+20\d{{2}})|(?:\d{{1,2}}\s+[A-Za-zÀ-ת]+\s+20\d{{2}}))", subject+"\n"+"\n".join(lines), re.I)
            if match: raw,line=match.group(1),match.group(0)
        val,conf=_date(raw); facts[f]=_fact(val,line,conf)
    intent="recommendation" if no or (rec and not strong) else "policy" if policy and not strong else "reservation" if strong else "ambiguous"
    required=(facts["hotel"],facts["check_in"],facts["check_out"]); decision="accept" if intent=="reservation" and all(x["value"] and x["confidence"]>=.9 for x in required) else "review" if intent=="reservation" else "reject"
    return {"intent":intent,"decision":decision,"facts":facts}

extract_semantic_hotel=understand_hotel_reservation
