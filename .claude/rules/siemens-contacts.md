---
paths:
  - "core/siemens_contacts.py"
  - "core/directory_api.py"
  - "tests/test_siemens_contacts.py"
---

## Relevant Siemens Contact: the Directory, read as an inference

Empower → Tool fit ends with "Relevant Siemens Contact" instead of a second evidence list
(`POST /api/runs/{id}/lookup/contacts` → `core/siemens_contacts.py` → `core/directory_api.py`).
- **Input:** each cited tool's name, category and department, which is the catalogue's `division`.
  The department is **shown as supplied**; a high-level one ("DI", "FT") is never replaced by a
  guessed sub-department.
- **The directory:** `GET /people`, API key in a header, filtered by department, low limit, cursor
  paging. It restarts from the beginning once on an invalid cursor, as the docs require. Only
  `commonName`, `department` and `email` leave the client, and any may be null.
- **Ranking:** a department matches people, not tool owners, so the people are **ranked by the app**:
  - an exact or deeper match of the tool's department first;
  - then departments naming a word of the tool's name or category;
  - then the more specific department.

  The top 3 are shown, under a note that says this is an inference. Divisions written as names
  ("Digital Industries Software") are searched as their code, and the row says so.
- **No key:** "not configured", never an empty list.
- **Testing:** the key, like GlassDollar's, resolves only inside the Siemens network, so the client's
  contract is driven with a fake (`tests/test_siemens_contacts.py`). Header, parameter and cursor
  names are env-configurable because only the endpoint is documented here.
- **Caching:** per tool and department in the web cache for 7 days. Contacts belong to a tool, not
  to the company being evaluated.
