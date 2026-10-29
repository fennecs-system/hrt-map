# hrt-map search agent spec

You feed a live map of **doctors and prescribers who provide gender-affirming hormone therapy (HRT)** in greater
Melbourne, VIC: GPs, endocrinologists, sexual health physicians, nurse practitioners, and clinics/services that prescribe.

## Match criteria
Include a practitioner only if their public info says they provide trans / gender-diverse / non-binary care or
gender-affirming hormones (HRT / GAHT / "informed consent" hormone prescribing). Menopause-only HRT does NOT count.
Melbourne metro only (incl. telehealth prescribers based in Melbourne/Victoria). One record per named clinician where
possible; if a clinic doesn't name its prescribers, one record for the clinic (name = clinic name).

## Fetching — use a normal browser user agent
WebFetch gets blocked (403) by many sites. When that happens, fetch with curl and a real browser UA:

    curl -sL --compressed -m 25 \
      -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" \
      -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
      -H "Accept-Language: en-AU,en;q=0.9" \
      "<url>" > /private/tmp/claude-502/hrt-fetch/<agent-id>/<something>.html

Save pages under /private/tmp/claude-502/hrt-fetch/<agent-id>/ (mkdir -p) and extract with grep/sed/python3 -I rather
than dumping whole pages into context. Many sites (HotDoc, Healthshare, HealthEngine) embed JSON in the HTML
(`__NEXT_DATA__`, `data-profile`, ld+json) — parse that. Public pages and the public JSON endpoints a page itself calls
are fine; nothing behind a login, no CAPTCHAs, no accepting usage agreements. ≥1s between requests to the same host.
Psychology Today hard-blocks this machine (403) — skip it. Don't run downloaded code.

## Output — write AS YOU GO (every 3–8 people, several per file), the user watches pins appear live
Use the Write tool to create a NEW file each time: /Users/fennecs/hrt-map/ingest/inbox/<agent-id>-<n>.json (n=1,2,3…).
Content: JSON array of objects. Use "" / false when unknown — NEVER invent:

    {"name": "", "pronouns": "only if published, exactly as written",
     "title": "e.g. Dr / GP / Endocrinologist", "role": "GP | Endocrinologist | Sexual health physician | Nurse practitioner | Clinic",
     "practice": "", "address": "street address", "suburb": "", "postcode": "", "phone": "", "email": "", "website": "",
     "booking_url": "direct booking link (HotDoc / HealthEngine / Cliniko / practice booking page)",
     "description": "their own words, 1-5 sentences — must say what trans / gender-affirming / HRT care they offer",
     "specialties": "comma list", "telehealth": true/false, "telehealth_only": true/false,
     "informed_consent": true only if they explicitly say informed-consent model,
     "bulk_billing": true only if they explicitly say they bulk bill (for this care),
     "min_age": "e.g. '18+' or '16+' if stated",
     "identity": "ONLY the practitioner's own self-description of their identity, quoted; else empty",
     "source_url": ""}

Identity rule: do NOT infer identity from pronouns or from who they treat. Only quote what they say about themselves.

Re-adding someone already on the map is fine when you have richer details — the ingest merges by name. Check with:
    python3 -c "import json;print('\n'.join(p['name']+' | '+p['suburb'] for p in json.load(open('/Users/fennecs/hrt-map/docs/data.json'))['people']))" | grep -i <name>

Final reply: one short paragraph: count written, sources that worked, sources that stayed blocked.
