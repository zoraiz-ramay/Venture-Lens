"""Shared text-processing helpers and the stopword set used across the engine.

Pure functions, no I/O: funding/money/headcount parsing (read by profile, traction, score and
market), the customer-name and source-URL gates, and person-name identity for deduplicating
founders and team across sources."""
from __future__ import annotations

import re
import unicodedata

_STOP = set("""a an the and or of for to in on with from your you our we it is are be as by at this that solution
platform software company startup using use uses based help helps enable enables provide provides product products
service services data ai ml technology tech into across their them they his her are can will more most than then
app apps mobile web online cloud saas b2b b2c consumer user users customer customers market markets team world
first leading global new tool tools system systems digitally smart real time end manage management""".split())


# A funding string is only useful when it names a stage or an amount. Anything else — "raised
# funding", or Crunchbase's "Unfunded" status label — is not a fact a reviewer can act on, and
# letting such a value through blocks the research pipeline from filling the field with a real,
# sourced round (makkook.ai showed "Unfunded" while its Crunchbase Pre-Seed went unreported).
FUNDING_SIGNAL = re.compile(
    r"[$€£]|\b\d|\bpre[-\s]?seed\b|\bseed\b|\bseries\s+[a-z]\b|\bangel\b|\bgrant\b|"
    r"\bbridge\b|\bipo\b|\bventure\b", re.I)


# Sites that publish modelled revenue for every company (data brokers and lead databases), and the
# words such estimates are written in. The prompt has always said third-party estimates are not
# revenue, but only the model enforced it: Gemini 2.5 Flash took SignalHire's "The annual revenue of
# Tridiagonal Solutions varies between 50M and 100M" as revenue — 30 traction points — while the
# gpt-6 models left it out as the rule says. Python decides now — at extraction (profile._clean_revenue) and again in the traction score, which
# is recomputed on every read, so stored runs are corrected too — and every model scores it alike.
_ESTIMATE_HOSTS = ("growjo.", "owler.", "zoominfo.", "signalhire.", "rocketreach.", "leadiq.", "apollo.io",
                   "konaequity.", "zippia.", "craft.co", "lusha.", "datanyze.", "kaspr.", "dnb.com", "zoominfo",
                   "companiesmarketcap.", "macrotrends.", "ecdb.", "6sense.")
_ESTIMATE_WORDS = re.compile(r"\bestimat|\bvaries between\b|\brevenue range\b|\bis projected\b|\bmodell?ed\b", re.I)


def is_revenue_estimate(quote: str, url: str) -> bool:
    """A revenue statement that is a data broker's model or worded as an estimate: never revenue."""
    host = re.sub(r"^https?://(www\.)?", "", str(url or "").lower()).split("/")[0]
    return any(h in host for h in _ESTIMATE_HOSTS) or bool(_ESTIMATE_WORDS.search(str(quote or "")))


def has_funding_signal(value) -> bool:
    """True when a funding string names a stage or an amount."""
    return bool(FUNDING_SIGNAL.search(str(value or "")))


def format_funding(value) -> str:
    """Render a raw funding amount compactly; pass any non-numeric string through unchanged.

    Both sources that carry an amount give a bare number in currency units — the API as a
    bigint, the applications xlsx as a spreadsheet cell — so "2831100.0" reached the profile
    verbatim and a reviewer had to count digits. Free text ("Pre-Seed, amount undisclosed")
    is already the most precise statement available and must survive untouched.

    The € is inherited from the API mapping this used to live in, and is an assumption: no
    source states a currency. It is applied consistently rather than to one source only.
    """
    if value in (None, "", 0):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if amount >= 1_000_000_000:
        return f"€{amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"€{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"€{amount / 1_000:.0f}K"
    return f"€{amount:.0f}"


def parse_funding_amount(value) -> float:
    """The currency amount a funding string states, or 0.0 when it states none.

    Scoring used to ask only whether a funding string *existed*, which made "€250K pre-seed"
    and "$1.4B Series F" the same signal. It also missed the amount entirely for GlassDollar
    rows, whose funding cell is a bare number that `FUNDING_SIGNAL` does not match at all.

    A bare number in prose is ignored unless it carries a currency symbol or a magnitude
    suffix — "Seed round, 2023" would otherwise read as a 2023-unit raise. The whole-string
    case is exempt because that is exactly the shape the xlsx cell and the API bigint take.
    Like `format_funding`, this returns a magnitude and not a currency; `parse_money` keeps it.

    Delegates to `parse_money`, whose own notation handling this used to lack: Tracxn's
    "USD 2500000" (an ISO code rather than a symbol) and "2,5 Mio €" both read as 0 here. A range
    now reads as its lower bound, as the traction rubric does.
    """
    money = parse_money(value)
    return max(0.0, money["low"]) if money else 0.0


# ---------------------------------------------------------------------------- money
# `parse_funding_amount` answers "how big" and deliberately drops the currency. The traction rubric
# needs the currency too — its bands are in euros, and the sources disagree: GlassDollar hands back
# a bare bigint, Tracxn "USD 2500000", the web "2,5 Mio €" or "₹15 crore". A parser that read none
# of those three would score them all as unfunded, which is the "unknown rendered as zero" mistake.
_SYMBOLS = {"us$": "USD", "ca$": "CAD", "c$": "CAD", "au$": "AUD", "a$": "AUD", "s$": "SGD",
            "hk$": "HKD", "$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
_ISO = ("USD", "EUR", "GBP", "CHF", "SAR", "AED", "INR", "JPY", "CNY", "RMB", "SEK", "NOK",
        "DKK", "PLN", "CAD", "AUD", "SGD", "HKD", "ILS", "BRL", "KRW", "QAR", "TRY")
_CURRENCY_WORDS = {"euro": "EUR", "euros": "EUR", "dollar": "USD", "dollars": "USD",
                   "pounds": "GBP", "rupees": "INR"}
_MONEY_MAG = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mln": 1e6, "mio": 1e6, "million": 1e6,
              "millions": 1e6, "b": 1e9, "bn": 1e9, "bln": 1e9, "mrd": 1e9, "billion": 1e9, "billions": 1e9,
              "trillion": 1e12, "trillions": 1e12, "tn": 1e12,
              "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
              "crore": 1e7, "crores": 1e7, "cr": 1e7}
# An ISO code may be glued to its number ("USD2M"), so it ends at the next non-letter, not at \b.
_CUR = (r"(?:US\$|CA\$|C\$|AU\$|A\$|S\$|HK\$|[$€£₹¥]|(?-i:\b(?:" + "|".join(_ISO)
        + r")(?![A-Za-z])))")
# Space-grouped thousands first ("750 000", French and German style), then ordinary digits, then a
# bare decimal (".5M") whose leading point would otherwise be dropped — a tenfold error.
_MNUM = r"\d{1,3}(?:[ \u202f]\d{3})+(?!\d)|\d[\d.,]*\d|\d|\.\d+"
# Longest alternatives first, so "m" never wins against "million" or "mio".
_MMAG = (r"(?:trillions|trillion|millions|million|thousand|billions|billion|crores|crore|lakhs|lakh|lacs|lac"
         r"|mrd|mio|mln|mn|bln|bn|tn|cr"
         r"|[kmb])\b\.?")
_MONEY = re.compile(
    # "$15.2-billion" hyphenates the magnitude; without the optional hyphen it read as 15.2 USD.
    rf"(?P<c1>{_CUR})?\s*(?P<n1>{_MNUM})(?:\s*-\s*(?={_MMAG})|\s*)(?P<m1>{_MMAG})?"
    rf"(?:\s*(?:-|–|—|to)\s*(?P<c2>{_CUR})?\s*(?P<n2>{_MNUM})\s*(?P<m2>{_MMAG})?)?"
    # A trailing currency must not be followed by a number: in "Seed 2023 $2.3M" that "$" belongs
    # to 2.3M, and taking it made the year a USD 2023 and left the real amount without a currency.
    rf"\s*(?P<c3>(?:{_CUR}|\beuros?\b|\bdollars?\b|\bpounds\b|\brupees\b)(?!\s*[\d.]))?", re.I)


def _money_number(s: str, has_magnitude: bool) -> float | None:
    """One number in any of the notations the sources use, or None.

    "2.500.000" and "2,5" are European; "2,500,000" and "2.5" are English. A lone comma group of
    exactly three digits ("2,500") is read as thousands unless a magnitude follows, because
    "2,500 Mio" is not a thing anyone writes but "2,5 Mio" is.
    """
    s = re.sub(r"[  ]", "", s).rstrip(".,")
    if s.startswith("."):                                     # .5 (M)
        return float("0" + s) if re.fullmatch(r"\.\d+", s) else None
    if re.fullmatch(r"\d{1,2}(?:,\d{2})+,\d{3}", s):          # 2,50,000 (Indian grouping)
        return float(s.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", s):           # 2.500.000,50
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d+", s):           # 2,500,000.50
        return float(s.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", s) or (
            re.fullmatch(r"\d{1,3}\.\d{3}", s) and not has_magnitude):
        return float(s.replace(".", ""))                      # 2.500.000 / 1.500 (no suffix)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", s) and not (has_magnitude and s.count(",") == 1):
        return float(s.replace(",", ""))                      # 2,500,000 / 2,500
    if re.fullmatch(r"\d+,\d+", s):
        return float(s.replace(",", "."))                     # 2,5 (Mio)
    try:
        return float(s)
    except ValueError:
        return None


def _currency_of(token: str | None) -> str:
    """ISO code for a currency symbol, code or word ('' when unrecognised); RMB reads as CNY."""
    t = (token or "").strip()
    if not t:
        return ""
    if t.upper() in _ISO:
        return "CNY" if t.upper() == "RMB" else t.upper()
    return _SYMBOLS.get(t.lower(), "") or _CURRENCY_WORDS.get(t.lower(), "")


def find_money(value) -> list[dict]:
    """Every amount a string states, each as {low, high, currency, currency_assumed, text}.

    A range ("$1-2M", "€1–2 Mio") keeps both ends: the rubric scores the lower one, because the
    upper end is exactly the kind of number a startup rounds up to. A number with neither a
    currency nor a magnitude is not an amount ("Seed round, 2023"), except when it is the whole
    string — that is the shape of GlassDollar's bigint and the xlsx cell. Neither of those states
    a currency, so it is reported as '' with ``currency_assumed`` for the caller to resolve.
    """
    text = str(value or "").replace("\u00a0", " ").strip()
    if not text:
        return []
    if re.fullmatch(r"\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        amount = float(text.replace(",", ""))
        return [{"low": amount, "high": amount, "currency": "", "currency_assumed": True,
                 "text": text, "start": 0, "end": len(text)}] if amount > 0 else []
    out = []
    for m in _MONEY.finditer(text):
        mag1 = (m.group("m1") or "").lower().rstrip(".")
        mag2 = (m.group("m2") or "").lower().rstrip(".")
        currency = next((c for c in (_currency_of(m.group(g)) for g in ("c1", "c2", "c3")) if c), "")
        if not currency and mag1 in ("lakh", "lakhs", "lac", "lacs", "crore", "crores", "cr"):
            currency = "INR"            # nobody counts in lakh and crore in any other currency
        if not (currency or mag1 or mag2):
            continue
        n1 = _money_number(m.group("n1"), bool(mag1 or mag2))
        if n1 is None:
            continue
        # "$1-2M": the suffix after the second number applies to both.
        f1 = _MONEY_MAG.get(mag1 or mag2, 1.0)
        low = high = n1 * f1
        # "$5M - 2023" is an amount and a year, not a five-to-two-thousand-million range.
        if m.group("n2") and not (not mag2 and re.fullmatch(r"(?:19|20)\d\d", m.group("n2"))):
            n2 = _money_number(m.group("n2"), bool(mag1 or mag2))
            if n2 is not None:
                high = n2 * _MONEY_MAG.get(mag2 or mag1, 1.0)
                low, high = min(low, high), max(low, high)
        if high <= 0:
            continue
        out.append({"low": low, "high": high, "currency": currency,
                    "currency_assumed": not currency, "text": m.group(0).strip(),
                    "start": m.start(), "end": m.end()})
    return out


def first_money(value) -> dict | None:
    """The first amount a string states, or None. A market figure states its base first and its
    forecast after ("USD 15.2B in 2024, reaching USD 25B by 2030"); the largest would be the forecast."""
    found = find_money(value)
    return min(found, key=lambda a: a["start"]) if found else None


def parse_money(value) -> dict | None:
    """The largest amount a string states (by its lower bound), or None.

    Largest, like `parse_funding_amount`: a funding line that names a round and a total ("Seed
    $2.5M, $4M raised to date") is best read by its total, and any single round is a floor on it.
    """
    found = find_money(value)
    return max(found, key=lambda a: a["low"]) if found else None


def parse_headcount(value) -> dict | None:
    """A headcount as {low, high}, or None when the text states no usable count.

    "11-50" is a band and scores by its lower bound; "10+" is a floor; "<10" names no lower bound
    at all and is therefore unknown rather than read as ten.
    """
    text = str(value or "").replace("\u00a0", " ").strip()
    if not text or re.match(r"^\s*(?:<|under|fewer than|less than)", text, re.I):
        return None
    nums = []
    for n, mag in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b|thousand\b)?", text, re.I):
        try:
            nums.append(float(n.replace(",", "")) * (1000 if mag else 1))
        except ValueError:
            continue
    if not nums:
        return None
    return {"low": int(min(nums)), "high": int(max(nums))}


# Funding STAGE, which is a different fact from funding amount and answers a different question.
# The amount says how much was raised; the stage says whether an institution underwrote it, which
# is what Siemens Financial Services' corporate-lending line and the Collaborate readiness check
# actually turn on — a €2M seed from angels and a €2M Series A from a fund are not the same
# counterparty risk. Ordered most-specific first: "pre-seed" must be tested before "seed", and
# "series a" before either, or every round collapses to seed.
_STAGE_PATTERNS = (
    ("series_b_plus", r"\bseries\s+[b-z]\b|\bgrowth\s+(?:round|equity|capital)\b|\bipo\b"
                      r"|\blate[-\s]stage\b|\bmezzanine\b"),
    ("series_a",      r"\bseries\s+a\b"),
    ("pre_seed",      r"\bpre[-\s]?seed\b|\bangel\b|\bfriends\s+and\s+family\b"),
    ("seed",          r"\bseed\b|\bbridge\b"),
    ("grant",         r"\bgrant\b|\bnon[-\s]dilutive\b|\bsubsidy\b|\bprize\b"),
)


def parse_funding_stage(value) -> str:
    """The funding stage a string names, or '' when it names none.

    Deterministic on purpose. The stage is already written in the funding strings the pipeline
    has painstakingly grounded ("Pre-Seed, amount obfuscated", "Seed, $2.5M (2024)"), so asking
    a model to re-derive it would add a call, a failure mode and a source of run-to-run drift
    to recover a fact already in hand.
    """
    text = str(value or "")
    if not text.strip():
        return ""
    for stage, pattern in _STAGE_PATTERNS:
        if re.search(pattern, text, re.I):
            return stage
    return ""


# What a customer list split out of free text leaves behind when it is not a name: the start of a
# clause ("In parallel", "For scale-up", "as well as …"), a description of a kind of buyer
# ("Chemical producers", "Materials and polymer value chains"), a half-closed bracket, or a
# sentence that ran on ("… and suppliers. Currently"). An application form's "Reference
# customers" box is often prose, and every piece of it used to be kept as a named account.
_CLAUSE_START = re.compile(
    r"^(?:in|for|to|with|and|or|as|at|on|of|by|from|over|under|about|around|approx(?:imately)?|"
    r"currently|including|include|includes|such|e\.?g\.?|i\.?e\.?|our|we|they|also|plus|more|"
    r"most|some|many|few|several|all|each|other|others|both|either|various|mainly|mostly|"
    r"primarily|especially|partly|then|than|while|when|where|which|who|this|these|those|"
    r"it|its|there|here|now|soon|already|still|yet|not|no|n/?a|tbd|none|up|down|over)\b", re.I)
_BUYER_KIND = re.compile(
    r"\b(?:factor(?:y|ies)|sectors?|industr(?:y|ies)|companies|clients?|customers?|enterprises?|"
    r"manufacturers?|producers?|suppliers?|operators?|providers?|owners?|outfits?|agenc(?:y|ies)|"
    r"organi[sz]ations|institutions|utilities|municipalities|hospitals|retailers|distributors|"
    r"startups?|businesses|firms|brands|smbs?|smes?|value chains?|end[- ]users?|consumers|"
    r"segments)\b", re.I)


def is_named_org(value) -> bool:
    """True when a string reads as the name of one organisation, not a fragment of prose.

    Deliberately strict: a real customer dropped here costs a few traction points, while a
    fragment kept is shown to a reviewer as a named account and scored as one.
    """
    s = str(value or "").strip().strip(".,;:")
    if not s or len(s) > 60 or not re.search(r"[A-Z]", s):
        return False
    if s.count("(") != s.count(")") or s.count("[") != s.count("]"):
        return False
    if re.search(r"[.!?]\s+\S", s) or re.search(r"\d", s) and not re.search(r"[A-Za-z]{2,}", s):
        return False
    return not (_CLAUSE_START.search(s) or _BUYER_KIND.search(s))


def _norm(s: str) -> str:
    """Lower-case alphanumerics separated by single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _keywords(text: str) -> set[str]:
    """Distinct meaningful words: longer than two letters and not in _STOP."""
    return {w for w in _norm(text).split() if len(w) > 2 and w not in _STOP}


def _split_list(s: str) -> list[str]:
    """Split a free-text list on `;`, `,` or `/`, dropping blanks."""
    return [x.strip() for x in re.split(r"[;,/]", str(s)) if x.strip()]


def _clean_source_url(value) -> str:
    """Keep a real http(s) link, otherwise ''.

    Asked for a source_url, the model sometimes answers with the corpus label it read the fact
    from ('f1, f2') rather than the link. Those reach the UI and render as a broken 'web-sourced'
    link, so anything that is not a URL is dropped — the fact is still kept, just without a
    citation. Mirrors the guard in profile._clean_employee_series.

    Lives here rather than in core/profile.py because core/trend.py's market landscape holds every
    competitor and funded peer to the same bar, and a second copy of a two-line grounding rule is
    exactly how the two drift apart. core.profile re-exports it, so existing imports still work.
    """
    url = str(value or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


# ------------------------------------------------------------------------------------ people
# Honorifics and post-nominals research sources attach to a name. "Dr. Andreas Wagner" and
# "Andreas Wagner" are one founder, and keeping both showed the same person twice on a profile.
# Only tokens that are never a name on their own: "Ma" is a surname, so no "MA" degree.
_PERSON_AFFIXES = frozenset("dr prof professor mr mrs ms dipl ing phd mba msc bsc jr sr dott".split())

# What a model writes where a name should be when the source did not give one. Radical Dot's third
# refresh stored "Alex (surname not stated)" and "Andi (surname not stated)" as founders beside
# "Alexandre Kremer" and "Andreas Wagner": a placeholder is not part of anyone's name.
_NAME_PLACEHOLDER = re.compile(
    r"\b(?:not|never)\s+(?:stated|given|named|known|specified|provided|disclosed|mentioned|available|listed|found)\b"
    r"|\b(?:sur|last|family|first|full|given)\s*-?\s*name\b|\bunknown\b|\bunnamed\b|\bundisclosed\b"
    r"|\bunspecified\b|\bn/a\b|\btbd\b|\banonymous\b", re.I)
# Words that make up a role, not a name: "CEO", "Co-founder", "Managing Director".
_ROLE_WORDS = frozenset("""ceo cto coo cfo cmo cpo cso cro cio chief officer founder founders cofounder co
founding managing director directors partner partners president vp vice head lead member team advisor
adviser advisors board chair chairman chairwoman engineer scientist manager executive the and of a an
""".split())

# Common short forms of a given name, each mapped to the full names it is short for. Kept small and
# to pairs that are unambiguous in practice; a short form that is also a plain prefix ("Alex" of
# "Alexandre") needs no entry. A match still has to be unique among the people on record.
_SHORT_FORMS = {
    "andi": {"andreas", "andrew"}, "andy": {"andreas", "andrew"}, "alex": {"alexander", "alexandre", "alexandra"},
    "sasha": {"alexander", "alexandra"}, "max": {"maximilian", "maxim"},
    "chris": {"christian", "christopher", "christoph"}, "tom": {"thomas"}, "tommy": {"thomas"},
    "mike": {"michael"}, "micha": {"michael"}, "matt": {"matthias", "matthew", "mathias"},
    "nick": {"nicholas", "nicolas", "nikolaus"}, "niko": {"nikolaus", "nikolai"},
    "rob": {"robert"}, "bob": {"robert"}, "bill": {"william"}, "will": {"william"},
    "jim": {"james"}, "joe": {"joseph", "josef"}, "steve": {"stephen", "steven", "stefan"},
    "dave": {"david"}, "kate": {"katharina", "katherine", "catherine", "kathrin"},
    "liz": {"elizabeth", "elisabeth"}, "basti": {"sebastian"}, "seb": {"sebastian"},
    "tony": {"anthony", "anton", "antonio"}, "toni": {"anton", "antonio"}, "fred": {"frederik", "friedrich", "frederick"},
    "flo": {"florian"}, "johnny": {"john", "johannes"}, "hannes": {"johannes"},
}


def person_key(name) -> str:
    """The identity of a person's name: diacritics folded, case dropped, a parenthetical role
    ("Jane Doe (CEO)") and honorifics/post-nominals removed. Empty when nothing identifying is left."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)
    text = text.split(",")[0]                       # "Jane Doe, PhD" / "Jane Doe, CEO"
    words = [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in _PERSON_AFFIXES]
    return " ".join(words)


def clean_person_name(name) -> str:
    """The name as written, without bracketed qualifiers or placeholder clauses; '' when no name is left.

    "Alex (surname not stated)" is "Alex"; "Jane Doe (CEO)" is "Jane Doe"; "Founder (name not
    given)" and "Unknown" are no name at all. A name is never completed or guessed here.
    """
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", str(name or ""))
    text = re.sub(r"[(\[{].*$", " ", text)                  # an unclosed bracket runs to the end
    parts = re.split(r"\s*(?:[,;]|\s[-–—]\s)\s*", text)
    kept = [parts[0]] + [p for p in parts[1:] if p.strip() and not _NAME_PLACEHOLDER.search(p)]
    text = re.sub(r"\s+", " ", ", ".join(p.strip() for p in kept if p.strip())).strip(" ,;:-–—.")
    if not text or _NAME_PLACEHOLDER.search(parts[0]):
        return ""
    words = person_key(text).split()
    if words and all(w in _ROLE_WORDS or w in _PERSON_AFFIXES for w in words):
        return ""
    return text


def is_full_name(name) -> bool:
    """At least two name tokens. A name with no Latin letters cannot be judged by token count
    (Chinese names are written without spaces), so it counts as full."""
    key = person_key(name)
    return not key or len(key.split()) >= 2


def _given_name(key: str) -> str:
    """The first token that is not an initial ("KD Kutadgu Gokalp Demirci" → kutadgu)."""
    tokens = key.split()
    return next((t for t in tokens if len(t) > 2), tokens[0] if tokens else "")


def short_name_matches(short, full) -> bool:
    """Whether a one-token name can be the given name of a full name: the same word, a prefix of it
    of three letters or more ("Alex" of "Alexandre"), or a listed short form ("Andi" of "Andreas")."""
    s = person_key(short.get("name") if isinstance(short, dict) else short)
    given = _given_name(person_key(full.get("name") if isinstance(full, dict) else full))
    if not s or " " in s or not given:
        return False
    return s == given or (len(s) >= 3 and given.startswith(s)) or given in _SHORT_FORMS.get(s, ())


def resolve_short_name(item, people):
    """The one full-name entry in ``people`` a one-token ``item`` names, or None.

    None when no entry matches, and also when more than one does: "Alex" beside "Alexandre Kremer"
    and "Alexander Ochs" could be either, and merging with the wrong one would put one person's
    background under another's name. Deterministic: the answer depends only on who is on record.
    """
    matches = [p for p in people or () if isinstance(p, dict) and is_full_name(p.get("name"))
               and person_key(p.get("name")) and short_name_matches(item, p)]
    return matches[0] if len(matches) == 1 else None


def _identity_link(item: dict) -> str:
    """A person's own profile link, normalised: the `linkedin` value, or a LinkedIn profile used as
    the source. Not any other source_url — a team page names everyone on it."""
    for value, any_link in ((item.get("linkedin"), True), (item.get("source_url"), False)):
        text = str(value or "").strip().casefold()
        if not text or (not any_link and "linkedin.com/in/" not in text):
            continue
        return re.sub(r"^https?://(www\.)?|/+$", "", text)
    return ""


def same_person(a, b) -> bool:
    """Whether two people entries are one person.

    Name keys match, or they differ only by initials ("KD Kutadgu Gokalp Demirci" and "Kutadgu
    Gokalp Demirci" — Phena's founder, stored twice because the initials defeated the name key), or
    both carry the same profile link and share a name token. The shared token is required because
    research has attached one person's LinkedIn to a colleague (Radical Dot's "Florian Zahn" carried
    Anna Winiwarter's), and merging those would lose a real person.

    Pairwise and strict: "Alex" is not judged against "Alexandre Kremer" here, because whether a
    first name alone is that person depends on who else is on record (``resolve_short_name``).
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    ka, kb = person_key(a.get("name")), person_key(b.get("name"))
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = ka.split(), kb.split()
    # Initials are tokens of one or two letters; what is left must still be a full name.
    fa, fb = [t for t in ta if len(t) > 2], [t for t in tb if len(t) > 2]
    if len(fa) >= 2 and fa == fb:
        return True
    la, lb = _identity_link(a), _identity_link(b)
    return bool(la) and la == lb and bool(set(ta) & set(tb))


# A background that says only that there is none: the evaluation's extraction wrote "Dr. Andreas
# Wagner; no prior companies or roles are specified in the results." for Radical Dot's CEO. It is
# shown as nothing, and a found background replaces it. Here rather than in core/profile.py so
# core/carry_forward.py can merge people without importing the profile module.
_SAYS_NOTHING = re.compile(r"\b(?:not (?:specified|stated|mentioned|available|provided|given|found|disclosed)|"
                           r"no (?:prior|previous|further|other|specific) (?:compan|role|information|detail|background)|"
                           r"unknown|n/a)", re.I)

_HONORIFICS = {"dr", "prof", "mr", "mrs", "ms", "sir", "dame", "herr", "frau"}


def says_nothing(background, name: str = "") -> bool:
    """Filler, or nothing but the person's own name ("Dr. Andreas Wagner" was his "background")."""
    def words(text):
        text = "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c))
        return set(re.findall(r"[a-z0-9]+", text.casefold()))
    text = str(background or "")
    if _SAYS_NOTHING.search(text):
        return True
    return bool(text.strip()) and not (words(text) - words(name) - _HONORIFICS)


def fill_person(known: dict, found: dict) -> None:
    """What a later record knows about a person the earlier one listed thinly: a background the
    earlier one left blank, and a role that only adds to the earlier one ("Co-founder" becomes
    "Co-founder and CEO", but a different role never replaces one). Each carries its own source."""
    if says_nothing(known.get("background"), known.get("name", "")):
        known["background"] = ""
    if not str(known.get("background") or "").strip() and str(found.get("background") or "").strip() \
            and not says_nothing(found.get("background"), found.get("name", "")):
        known["background"] = str(found["background"]).strip()
        if found.get("background_source"):
            known["background_source"] = found["background_source"]
    old_role, new_role = str(known.get("role") or "").strip(), str(found.get("role") or "").strip()
    if new_role and len(new_role) > len(old_role) and old_role.casefold() in new_role.casefold():
        known["role"] = new_role
    for k in ("linkedin", "source_url"):
        if not str(known.get(k) or "").strip() and str(found.get(k) or "").strip():
            known[k] = found[k]


def merge_person(known: dict, found: dict) -> None:
    """``known`` keeps its name and every value it has; ``found`` fills its blanks (``fill_person``
    for the background and role, which have rules of their own, and any other blank field as is).
    Nothing is invented: every value comes from one of the two entries."""
    fill_person(known, found)
    for field, value in found.items():
        if field in ("name", "background", "background_source", "role", "last_confirmed_at"):
            continue
        if str(value or "").strip() and not str(known.get(field) or "").strip():
            known[field] = value


def dedupe_people(people) -> list:
    """One entry per named person, in first-seen order; a duplicate only fills blank fields.

    Names are cleaned first (``clean_person_name``) and an entry left with no name is dropped, so
    a placeholder is never stored as a person.

    A one-token name ("Alexandre", "Andi") is merged into the one full-name entry it names
    (``resolve_short_name``), which keeps the full name. One that names nobody, or more than one
    person, is dropped: the model wrote it from a passage that gave only a first name, so it cannot
    be checked against any source as a person, and every run spelled it differently — Radical Dot's
    founders went 2 → 3 → 5 across three refreshes on such entries though the company has two.
    Python decides this, not the prompt, so it holds whatever the model returns.
    """
    out: list = []
    singles: list = []
    for item in people or []:
        if not isinstance(item, dict):
            out.append(item)
            continue
        name = clean_person_name(item.get("name"))
        if not name:
            continue
        item = {**item, "name": name}
        bucket = out if is_full_name(name) else singles
        kept = next((p for p in bucket if isinstance(p, dict) and same_person(p, item)), None)
        if kept is None:
            bucket.append(item)
        else:
            merge_person(kept, item)
    named = [p for p in out if isinstance(p, dict)]
    for item in singles:
        match = resolve_short_name(item, named)
        if match is not None:
            merge_person(match, item)
    return out


def founders_first(profile: dict) -> dict:
    """Drop from key_team and advisors anyone already listed as a founder (founder wins), and from
    advisors anyone already in key_team. One person in two lists read as two people on a profile."""
    above: list = []
    for key in ("founders", "key_team", "advisors"):
        people = profile.get(key)
        if not isinstance(people, list):
            continue
        if above:
            profile[key] = [p for p in people if not any(same_person(p, q) for q in above)
                            and not (isinstance(p, dict) and not is_full_name(p.get("name"))
                                     and resolve_short_name(p, above) is not None)]
        above += [p for p in profile[key] if isinstance(p, dict)]
    return profile
