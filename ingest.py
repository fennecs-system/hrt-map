#!/usr/bin/env python3
"""Ingest loop (HRT / gender-affirming prescribers): watches ingest/inbox/*.json (written by search agents),
normalises, keyword-filters, dedupes, geocodes (Nominatim, cached) and
writes public/data.json, which the map polls."""
import json, re, time, hashlib, urllib.parse, urllib.request, pathlib, random, sys

ROOT = pathlib.Path(__file__).resolve().parent
INBOX = ROOT / "ingest" / "inbox"
REJECT = ROOT / "ingest" / "rejected"
CACHE = ROOT / "ingest" / "geocache.json"
OUT = ROOT / "docs" / "data.json"
MELB = (-37.8136, 144.9631)

KEYWORDS = {
    "trans": r"\btrans(gender|\b|-|\*)|gender[- ]diverse|non[- ]?binary|gender[- ]affirm|gender dysphoria|gender incongru|gender[- ]questioning",
    "hrt": r"\bhrt\b|\bg-?a-?h-?t\b|hormone(s| therap| replacement| treatment| prescri)|oestr|estr(a|o)|testosterone|anti-?androgen|puberty block",
    "informed-consent": r"informed[- ]consent",
    "youth": r"\byouth|adolescen|young people|under 18|children|paediatric|pediatric|puberty block",
    "lgbt": r"lgbt|queer|rainbow|gay|lesbian|bisexual",
    "sexual-health": r"sexual health|\bprep\b|\bstis?\b|\bhiv\b",
    "bulk-bill": r"bulk[- ]?bill",
}
# role tags come only from role/title (descriptions say things like "GP referral required")
ROLE_RE = {"gp": re.compile(r"\bgp\b|general practi", re.I), "endo": re.compile(r"endocrinolog", re.I),
           "np": re.compile(r"nurse practitioner|\bnp\b|\bmnp\b", re.I), "paeds": re.compile(r"paediatric|pediatric", re.I)}
# availability: explicit overrides win, else inferred from wording; closed > waitlist > open
AVAIL_RE = [
    ("closed", re.compile(r"not (currently )?(taking|accepting|seeing) (any )?new|closed to new|wait ?list (is )?(currently )?closed|"
                          r"(books|bookings) (are )?(currently )?closed|no new patients|paused new|on (parental|maternity|extended) leave|"
                          r"(isn't|is not|aren't|are not) (taking|accepting) new", re.I)),
    ("waitlist", re.compile(r"wait ?list|waiting list|wait(ing)? times?|booking (people|patients) referred", re.I)),
    ("open", re.compile(r"(now |currently )?(taking|accepting|welcom\w+) new (patients|clients)|new patients welcome", re.I)),
]
# a practitioner is only kept if they work in trans / gender-affirming care (excludes menopause-only HRT)
REQUIRED = "trans"
KW_RE = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}
TRANS_ID_RE = re.compile(r"\b(trans|transgender|non-?binary|nonbinary|enby|genderqueer|gender[- ]?fluid|agender|two[- ]spirit(ed)?)\b", re.I)

geocache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
last_geo = 0.0


def geocode(q):
    global last_geo
    if not q:
        return None
    if q in geocache:
        return geocache[q]
    wait = 1.1 - (time.time() - last_geo)
    if wait > 0:
        time.sleep(wait)
    last_geo = time.time()
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "au"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "psych-map-local/1.0"})
        res = json.loads(urllib.request.urlopen(req, timeout=15).read())
        hit = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
    except Exception as e:
        print("geocode error", q, e, file=sys.stderr)
        return None
    geocache[q] = hit
    CACHE.write_text(json.dumps(geocache))
    return hit


def in_melbourne(ll):
    return ll and -38.6 < ll[0] < -37.3 and 144.3 < ll[1] < 145.9


def norm_name(n):
    n = re.sub(r"\b(dr|mr|ms|mx|mrs|prof)\.?\s+", "", n.lower())
    return re.sub(r"[^a-z]", "", n)


def clean_url(u):
    if not u:
        return ""
    u = u.strip()
    if u and not u.startswith("http"):
        u = "https://" + u
    return u


def locate(r):
    sub_ll = None
    if r.get("suburb"):
        sub_ll = geocode(f"{r['suburb']} {r.get('postcode') or ''} VIC Australia".replace("  ", " "))
        if not in_melbourne(sub_ll):
            sub_ll = None
    if r.get("address"):
        addr = r["address"]
        if r.get("suburb") and r["suburb"].lower() not in addr.lower():
            addr = f"{addr}, {r['suburb']}"
        ll = geocode(f"{addr}, VIC, Australia")
        # trust the street hit only if it lands near the stated suburb (~3km)
        if in_melbourne(ll) and (not sub_ll or abs(ll[0] - sub_ll[0]) + abs(ll[1] - sub_ll[1]) < 0.04):
            return ll, "address"
    if sub_ll:
        return sub_ll, "suburb"
    try:
        ll = [float(r["lat"]), float(r["lng"])]
        if in_melbourne(ll):
            return ll, "agent"
    except Exception:
        pass
    if r.get("telehealth_only") or not (r.get("address") or r.get("suburb")):
        return list(MELB), "approx"
    return None, None


NICK = {"liz": "elizabeth", "beth": "elizabeth", "lizzie": "elizabeth", "kate": "katherine", "katie": "katherine",
        "catherine": "katherine", "cath": "katherine", "cathy": "katherine", "kathryn": "katherine", "jen": "jennifer", "jenny": "jennifer", "alex": "alexandra",
        "alexander": "alexandra", "sam": "samantha", "samuel": "samantha", "chris": "christopher", "christine": "christopher",
        "nick": "nicholas", "matt": "matthew", "mike": "michael", "rob": "robert", "bob": "robert", "dan": "daniel",
        "dave": "david", "tom": "thomas", "ben": "benjamin", "jo": "joanne", "steph": "stephanie", "nic": "nicola",
        "nicki": "nicola", "nikki": "nicola", "andy": "andrew", "tony": "anthony", "pip": "philippa", "mel": "melissa",
        "bec": "rebecca", "becky": "rebecca", "bek": "rebecca", "jess": "jessica", "jodie": "jodie", "will": "william"}
CREDS = r"\b(dr|mr|ms|mx|mrs|miss|prof|professor|associate|assoc|a/prof|phd|maps|faps|mpsych|dpsych|bsc|ba|ma|msc|clin|psych)\b\.?"
GENERIC_EMAIL = re.compile(r"^(info|admin|reception|hello|contact|enquiries|enquiry|office|bookings?|referrals?|team|mail|intake|clinic)@", re.I)
SELF_ID_RE = re.compile(
    r"\b(i am|i'm|as an?|identif(?:y|ies) as|is an?|being an?|myself as|themselves as|they are an?)\s+(?:[\w,]+\s+){0,3}?"
    r"(trans(?:gender)?|non-?binary|nonbinary|enby|genderqueer|gender ?fluid|agender|two[- ]spirit(?:ed)?)"
    r"(?![- ]?(affirm|inclusive|friendly|aware|clients?|communit|youth|young|people|folks?|individuals?|and |, |or |issues|health|experience|identit|populations?))", re.I)
PRON_TOKENS = {"she", "her", "hers", "he", "him", "his", "they", "them", "theirs", "their"}


def pron_tags(pron):
    pl = pron.lower()
    tags = []
    if re.search(r"\bshe\b|\bher\b", pl): tags.append("she/her")
    if re.search(r"\bhe\b|\bhim\b", pl): tags.append("he/him")
    if re.search(r"\bthey\b|\bthem\b", pl): tags.append("they/them")
    toks = set(re.findall(r"[a-z]+", pl)) - {"pronouns", "and", "or", "use"}
    if toks - PRON_TOKENS: tags.append("other")
    return tags


def name_tokens(n):
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.split(r",|\s[-–—|]\s", n)[0]
    n = re.sub(CREDS, " ", n.lower())
    toks = [NICK.get(t, t) for t in re.findall(r"[a-z][a-z'’-]*", n)]
    return [t.replace("'", "").replace("’", "") for t in toks]


def merge(a, b):
    for k, v in b.items():
        if k in ("sources", "identity_all", "avail_all"):
            continue
        if v and not a.get(k):
            a[k] = v
        elif k == "description" and v and len(v) > len(a.get(k, "")):
            a[k] = v
        elif k == "name" and len(v) > len(a[k]) and "(" not in v:
            a[k] = v
    a["sources"] = sorted(set(a.get("sources", [])) | set(b.get("sources", [])))
    a["identity_all"] = a.get("identity_all", "") + " " + b.get("identity_all", "")
    a["avail_all"] = a.get("avail_all", "") + " " + b.get("avail_all", "")
    a["added"] = min(a["added"], b["added"])
    return a


EXCLUDE = ROOT / "ingest" / "exclude.txt"


def excluded():
    if not EXCLUDE.exists():
        return set()
    names = (l.split("#")[0].strip() for l in EXCLUDE.read_text().splitlines())
    return {"".join(name_tokens(n)) for n in names if n}


ALIASES = ROOT / "ingest" / "aliases.txt"
OVERRIDES = ROOT / "ingest" / "overrides.json"


def aliases():
    out = {}
    if ALIASES.exists():
        for l in ALIASES.read_text().splitlines():
            l = l.split("#")[0]
            if "=>" in l:
                a, b = (x.strip() for x in l.split("=>", 1))
                out[a.lower()] = b
    return out


def overrides():
    try:
        return {"".join(name_tokens(k)): v for k, v in json.loads(OVERRIDES.read_text()).items()}
    except Exception:
        return {}


def build():
    skip = excluded()
    alias = aliases()
    recs = []
    for f in sorted(INBOX.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            raw = json.loads(f.read_text())
        except Exception:
            continue
        for r in raw if isinstance(raw, list) else [raw]:
            if not isinstance(r, dict) or not r.get("name"):
                continue
            text = " ".join(str(r.get(k, "")) for k in ("description", "specialties", "practice", "keywords", "title", "role"))
            kws = [k for k, rx in KW_RE.items() if rx.search(text)]
            if r.get("bulk_billing"): kws.append("bulk-bill")
            if r.get("informed_consent"): kws.append("informed-consent")
            if REQUIRED not in kws or "".join(name_tokens(str(r["name"]))) in skip:
                continue
            nm = re.sub(r"\s+", " ", str(r["name"])).strip()
            recs.append({
                "name": alias.get(nm.lower(), nm),
                "pronouns": str(r.get("pronouns") or "").strip(),
                "title": r.get("title") or "",
                "practice": r.get("practice") or "",
                "address": r.get("address") or "",
                "suburb": r.get("suburb") or "",
                "postcode": str(r.get("postcode") or ""),
                "phone": r.get("phone") or "",
                "email": (r.get("email") or "").strip().lower(),
                "website": clean_url(r.get("website")),
                "booking_url": clean_url(r.get("booking_url")),
                "description": str(r.get("description") or "")[:1500],
                "specialties": str(r.get("specialties") or ""),
                "role": str(r.get("role") or ""),
                "bulk_billing": bool(r.get("bulk_billing")),
                "informed_consent": bool(r.get("informed_consent")),
                "min_age": str(r.get("min_age") or ""),
                "telehealth": bool(r.get("telehealth") or r.get("telehealth_only")),
                "telehealth_only": bool(r.get("telehealth_only")),
                "lat": r.get("lat"), "lng": r.get("lng"),
                "keywords": kws,
                "identity_all": str(r.get("identity") or ""),
                "avail_all": str(r.get("description") or "") + " " + str(r.get("availability_note") or ""),
                "sources": [s for s in [r.get("source_url")] if s],
                "agent": f.name.split("-")[0],
                "added": int(f.stat().st_mtime),
            })
    # union-find dedup: same first+last name, same personal email, or same initial+surname at same suburb/practice
    parent = list(range(len(recs)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(i, j):
        parent[find(i)] = find(j)
    seen = {}
    for i, r in enumerate(recs):
        t = name_tokens(r["name"])
        if not t:
            continue
        keys = [("n", t[0], t[-1])]
        if len(t) > 1:
            for loc in (r["suburb"].lower(), r["practice"].lower()[:20]):
                if loc:
                    keys.append(("i", t[0][0], t[-1], loc))
        # only merge on email when it looks personal (local part contains one of their name tokens)
        local = r["email"].split("@")[0]
        if r["email"] and not GENERIC_EMAIL.match(r["email"]) and any(len(x) >= 3 and x in local for x in t + name_tokens(r["name"].replace("(", " ").replace(")", " "))):
            keys.append(("e", r["email"]))
        for k in keys:
            if k in seen:
                union(i, seen[k])
            else:
                seen[k] = i
    people = {}
    for i, r in enumerate(recs):
        root = find(i)
        people[root] = merge(people[root], r) if root in people else r
    ov = overrides()
    # "practice:<substring>" overrides apply to everyone whose practice name contains it (personal overrides win)
    try:
        pov = [(k.split(":", 1)[1].lower(), v) for k, v in json.loads(OVERRIDES.read_text()).items() if k.startswith("practice:")]
    except Exception:
        pov = []
    hidden = []
    for key in list(people):
        p = people[key]
        # hide prescribers whose only evidence is an AusPATH directory listing (membership != confirmed prescribing)
        if p["sources"] and all("auspath.org.au" in u for u in p["sources"]):
            hidden.append(p["name"]); del people[key]
    (ROOT / "ingest" / "hidden_unverified.txt").write_text(
        "# hidden: only source is the AusPATH directory (auto-generated)\n" + "\n".join(sorted(hidden)) + "\n")
    for p in people.values():
        for sub, vals in pov:
            if sub in (p["practice"] + " " + p["name"]).lower():
                for k, v in vals.items():
                    if not k.startswith("_"):
                        p[k] = v
        for k, v in ov.get("".join(name_tokens(p["name"])), {}).items():
            if not k.startswith("_"):
                p[k] = v
        if p.get("availability") not in ("open", "closed", "waitlist", "unknown"):
            p["availability"] = next((st for st, rx in AVAIL_RE if rx.search(p["avail_all"])), "unknown")
        p.pop("avail_all", None)
        p["trans_nb"] = bool(TRANS_ID_RE.search(p["identity_all"]) or SELF_ID_RE.search(p["description"]))
        p["identity"] = re.sub(r"\s+", " ", p.pop("identity_all")).strip()
        p["pron_tags"] = pron_tags(p["pronouns"])
        p["keywords"] = sorted(set(p["keywords"]) | {k for k, rx in KW_RE.items() if rx.search(" ".join((p["description"], p["specialties"], p["title"], p["role"])))}
                               | {k for k, rx in ROLE_RE.items() if rx.search(p["role"] + " " + p["title"])})
        p["_key"] = "".join(name_tokens(p["name"])) or norm_name(p["name"])
    out = []
    for p in people.values():
        key = p.pop("_key")
        ll, prec = locate(p)
        if not ll:
            REJECT.mkdir(exist_ok=True)
            continue
        # jitter so co-located practitioners don't stack exactly
        h = int(hashlib.md5(key.encode()).hexdigest(), 16)
        j = 0.0006 if prec == "address" else 0.004 if prec == "suburb" else 0.02
        p["lat"] = ll[0] + ((h % 1000) / 1000 - 0.5) * j
        p["lng"] = ll[1] + (((h // 1000) % 1000) / 1000 - 0.5) * j
        p["precision"] = prec
        p["id"] = key
        out.append(p)
    out.sort(key=lambda p: p["added"])
    meta = {"updated": int(time.time()), "count": len(out), "inbox_files": len(list(INBOX.glob("*.json")))}
    # slim index for map + list + filters; full records go in details.json, loaded on demand
    slim = []
    for p in out:
        v = hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]
        slim.append({k: p[k] for k in SLIM_FIELDS} | {"lat": round(p["lat"], 5), "lng": round(p["lng"], 5), "v": v})
    write_json(OUT, {**meta, "people": slim})
    write_json(OUT.with_name("details.json"), {"updated": meta["updated"], "people": {p["id"]: p for p in out}})
    write_json(OUT.with_name("meta.json"), meta)
    return len(out)


SLIM_FIELDS = ("id", "name", "role", "availability", "pronouns", "pron_tags", "trans_nb", "precision", "suburb", "practice",
               "keywords", "added", "telehealth_only")


def write_json(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


if __name__ == "__main__":
    last_sig = None
    while True:
        sig = tuple((f.name, f.stat().st_mtime) for f in [*INBOX.glob("*.json"), *[x for x in (EXCLUDE, ALIASES, OVERRIDES) if x.exists()]])
        if sig != last_sig:
            n = build()
            print(time.strftime("%H:%M:%S"), f"{len(sig)} files -> {n} practitioners", flush=True)
            last_sig = sig
        if "--once" in sys.argv:
            break
        time.sleep(2)
