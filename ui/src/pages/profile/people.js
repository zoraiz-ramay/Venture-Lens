/* One person, one entry. Research sources spell a founder several ways ("Dr. Andreas Wagner",
   "Andreas Wagner", "Alexandre Krémer (CTO)", "Alex (surname not stated)"), and runs stored before
   the engine deduplicated (core/text.py::dedupe_people) still hold every spelling — stored runs are
   never rewritten, so the profile collapses them as it reads. Mirrors core/text.py; keep the two in
   step. */

const AFFIXES = new Set("dr prof professor mr mrs ms dipl ing phd mba msc bsc jr sr dott".split(" "));

// What a model writes where a name should be when the source gave none (text.py::_NAME_PLACEHOLDER).
const PLACEHOLDER = new RegExp(
  String.raw`\b(?:not|never)\s+(?:stated|given|named|known|specified|provided|disclosed|mentioned|available|listed|found)\b`
  + String.raw`|\b(?:sur|last|family|first|full|given)\s*-?\s*name\b|\bunknown\b|\bunnamed\b|\bundisclosed\b`
  + String.raw`|\bunspecified\b|\bn\/a\b|\btbd\b|\banonymous\b`, "i");
const ROLE_WORDS = new Set(`ceo cto coo cfo cmo cpo cso cro cio chief officer founder founders cofounder co
founding managing director directors partner partners president vp vice head lead member team advisor
adviser advisors board chair chairman chairwoman engineer scientist manager executive the and of a an`.split(/\s+/));

// Short forms of a given name (text.py::_SHORT_FORMS). A plain prefix ("Alex" of "Alexandre") needs no entry.
const SHORT_FORMS = {
  andi: ["andreas", "andrew"], andy: ["andreas", "andrew"], alex: ["alexander", "alexandre", "alexandra"],
  sasha: ["alexander", "alexandra"], max: ["maximilian", "maxim"],
  chris: ["christian", "christopher", "christoph"], tom: ["thomas"], tommy: ["thomas"],
  mike: ["michael"], micha: ["michael"], matt: ["matthias", "matthew", "mathias"],
  nick: ["nicholas", "nicolas", "nikolaus"], niko: ["nikolaus", "nikolai"],
  rob: ["robert"], bob: ["robert"], bill: ["william"], will: ["william"],
  jim: ["james"], joe: ["joseph", "josef"], steve: ["stephen", "steven", "stefan"],
  dave: ["david"], kate: ["katharina", "katherine", "catherine", "kathrin"],
  liz: ["elizabeth", "elisabeth"], basti: ["sebastian"], seb: ["sebastian"],
  tony: ["anthony", "anton", "antonio"], toni: ["anton", "antonio"], fred: ["frederik", "friedrich", "frederick"],
  flo: ["florian"], johnny: ["john", "johannes"], hannes: ["johannes"],
};

export function personKey(name) {
  const text = String(name || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/\([^)]*\)|\[[^\]]*\]/g, " ").split(",")[0];
  return text.split(/[^a-z0-9]+/).filter((w) => w && !AFFIXES.has(w)).join(" ");
}

/** The name as written, without bracketed qualifiers or placeholder clauses; "" when no name is
    left ("Alex (surname not stated)" is "Alex"; "Founder (name not given)" is nothing). */
export function cleanPersonName(name) {
  let text = String(name || "").replace(/\([^)]*\)|\[[^\]]*\]|\{[^}]*\}/g, " ").replace(/[([{].*$/, " ");
  const parts = text.split(/\s*(?:[,;]|\s[-–—]\s)\s*/);
  const kept = [parts[0], ...parts.slice(1).filter((p) => p.trim() && !PLACEHOLDER.test(p))];
  text = kept.map((p) => p.trim()).filter(Boolean).join(", ").replace(/\s+/g, " ")
    .replace(/^[\s,;:\-–—.]+|[\s,;:\-–—.]+$/g, "");
  if (!text || PLACEHOLDER.test(parts[0])) return "";
  const words = personKey(text).split(" ").filter(Boolean);
  if (words.length && words.every((w) => ROLE_WORDS.has(w) || AFFIXES.has(w))) return "";
  return text;
}

/** At least two name tokens; a name with no Latin letters counts as full (it cannot be judged). */
export function isFullName(name) {
  const key = personKey(name);
  return !key || key.split(" ").length >= 2;
}

const nameOf = (item) => (typeof item === "string" ? item : item?.name);

function givenName(key) {
  const tokens = key.split(" ").filter(Boolean);
  return tokens.find((t) => t.length > 2) || tokens[0] || "";
}

/** Whether a one-token name can be the given name of a full name: the same word, a prefix of three
    letters or more ("Alex" of "Alexandre"), or a listed short form ("Andi" of "Andreas"). */
export function shortNameMatches(short, full) {
  const s = personKey(nameOf(short));
  const given = givenName(personKey(nameOf(full)));
  if (!s || s.includes(" ") || !given) return false;
  return s === given || (s.length >= 3 && given.startsWith(s)) || (SHORT_FORMS[s] || []).includes(given);
}

/** The one full-name entry in `people` a one-token `item` names, or null — also when it could be
    more than one person (text.py::resolve_short_name). */
export function resolveShortName(item, people) {
  const matches = (people || []).filter((p) => p && typeof p === "object" && isFullName(p.name)
    && personKey(p.name) && shortNameMatches(item, p));
  return matches.length === 1 ? matches[0] : null;
}

/* A person's own profile link, normalised: the `linkedin` value, or a LinkedIn profile used as the
   source — not any other source, since a team page names everyone on it. */
function identityLink(item) {
  for (const [value, anyLink] of [[item?.linkedin, true], [item?.source_url, false]]) {
    const text = String(value || "").trim().toLowerCase();
    if (!text || (!anyLink && !text.includes("linkedin.com/in/"))) continue;
    return text.replace(/^https?:\/\/(www\.)?|\/+$/g, "");
  }
  return "";
}

/** Whether two people entries are one person. Mirrors core/text.py::same_person: the name keys
    match, or differ only by initials ("KD Kutadgu Gokalp Demirci" — Phena's co-founder, listed
    twice), or both carry the same profile link and share a name token (a shared link alone once
    merged two colleagues, because research had attached one's LinkedIn to the other). Strict and
    pairwise: a first name alone is resolved against everyone on record (`resolveShortName`). */
export function samePerson(a, b) {
  const pa = typeof a === "string" ? { name: a } : a;
  const pb = typeof b === "string" ? { name: b } : b;
  const ka = personKey(pa?.name);
  const kb = personKey(pb?.name);
  if (!ka || !kb) return false;
  if (ka === kb) return true;
  const ta = ka.split(" ");
  const tb = kb.split(" ");
  const fa = ta.filter((t) => t.length > 2).join(" ");
  const fb = tb.filter((t) => t.length > 2).join(" ");
  if (fa.includes(" ") && fa === fb) return true;
  const la = identityLink(pa);
  return Boolean(la) && la === identityLink(pb) && ta.some((t) => tb.includes(t));
}

function fillBlanks(kept, item) {
  for (const [field, value] of Object.entries(item)) {
    if (field !== "name" && String(value ?? "").trim() && !String(kept[field] ?? "").trim()) kept[field] = value;
  }
}

/** One entry per named person, first-seen order; a duplicate only fills fields the kept entry left
    blank. A placeholder is no name; a first name alone joins the one full-name entry it names and
    is otherwise dropped (core/text.py::dedupe_people). */
export function dedupePeople(people) {
  const out = [];
  const singles = [];
  for (const raw of people || []) {
    if (!raw || typeof raw !== "object") {
      out.push(raw);
      continue;
    }
    const name = cleanPersonName(raw.name);
    if (!name) continue;
    const item = { ...raw, name };
    const bucket = isFullName(name) ? out : singles;
    const kept = bucket.find((p) => p && typeof p === "object" && samePerson(p, item));
    if (kept) fillBlanks(kept, item);
    else bucket.push(item);
  }
  const named = out.filter((p) => p && typeof p === "object");
  for (const item of singles) {
    const match = resolveShortName(item, named);
    if (match) fillBlanks(match, item);
  }
  return out;
}

/** `people` without anyone already in `above` — a founder also listed as an advisor is shown once,
    as a founder (core/text.py::founders_first). */
export function withoutListed(people, above) {
  const listed = (above || []).filter((q) => q && typeof q === "object");
  return (people || []).filter((p) => !listed.some((q) => samePerson(p, q))
    && !(p && typeof p === "object" && !isFullName(p.name) && resolveShortName(p, listed)));
}

/** A stable id per person for one rendering: entries that are the same person share an id, so a
    Map keyed on it holds each person once. A first name alone shares the id of the one full-name
    person seen so far that it names. */
export function personResolver() {
  const seen = [];
  return (item) => {
    const entry = typeof item === "string" ? { name: item } : item;
    let found = seen.find((s) => samePerson(s.item, entry));
    if (!found && !isFullName(entry?.name)) {
      const match = resolveShortName(entry, seen.map((s) => s.item));
      found = match ? seen.find((s) => s.item === match) : undefined;
    }
    if (found) return found.id;
    const id = personKey(nameOf(item)) || `#${seen.length}`;
    seen.push({ item: entry, id: `${id}#${seen.length}` });
    return seen[seen.length - 1].id;
  };
}
