"""Department profiles: Siemens' stated needs, example needs, or an admin's own.

`profiles()` is also called directly by api/main.py and api/assessments.py: every evaluation is
assessed for all of these departments.

A department's needs come from one of three places, and the profile says which (`source`):

- `workbook` — `data/Startup_Evaluator_Departmental_Requirements.xlsx` (core/department_needs.py),
  the needs Siemens itself defined. These are real needs, so the department is not `demo`, and
  Collaborate is assessed against each capability with its description, not a bare keyword.
- `demo` — the five example keywords below, used only when the workbook has nothing for the
  department (a checkout without the file). Labelled as examples everywhere they appear.
- `admin` — set through PUT by an admin (`is_demo=0`); never overwritten by a reseed.
"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from api.auth import Principal, current_user, require_admin
from api import store
from core.department_needs import load_department_requirements

router = APIRouter(prefix="/api/departments", tags=["departments"])
DEMO = [
    ("di", "Digital Industries", ["automation", "digital twin", "manufacturing", "inspection", "industrial software"]),
    ("si", "Smart Infrastructure", ["buildings", "energy", "grid", "electrification", "storage"]),
    ("mobility", "Siemens Mobility", ["rail", "transport", "signalling", "fleet", "maintenance"]),
]


def _seed():
    """(id, label, keywords) per department: the workbook's where it has the department."""
    book = load_department_requirements()
    return [(key, book[key]["label"], list(dict.fromkeys(k for n in book[key]["needs"] for k in n["keywords"])))
            if key in book else (key, label, terms) for key, label, terms in DEMO]


def _db():
    con = store._conn()
    con.executescript('''CREATE TABLE IF NOT EXISTS department_profiles (
      id TEXT PRIMARY KEY, label TEXT NOT NULL, interests TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 1);''')
    seed = _seed()
    con.executemany("INSERT OR IGNORE INTO department_profiles VALUES (?,?,?,1)",
                    [(key, label, json.dumps(terms)) for key, label, terms in seed])
    # Reseed only the rows no admin has configured (is_demo=1), so a workbook change reaches them.
    con.executemany("UPDATE department_profiles SET label=?, interests=? WHERE id=? AND is_demo=1",
                    [(label, json.dumps(terms), key) for key, label, terms in seed])
    con.commit()
    return con


class ProfileBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    interests: list[str] = Field(..., min_length=1, max_length=30)


@router.get("")
def profiles(user: Principal = Depends(current_user)):
    with _db() as con:
        rows = con.execute("SELECT * FROM department_profiles ORDER BY CASE id WHEN 'di' THEN 0 WHEN 'si' THEN 1 WHEN 'mobility' THEN 2 ELSE 3 END, id").fetchall()
    book = load_department_requirements()
    out = []
    for r in rows:
        from_book = bool(r[3]) and r[0] in book
        source = "admin" if not r[3] else "workbook" if from_book else "demo"
        out.append({"id": r[0], "label": r[1], "interests": json.loads(r[2]), "demo": source == "demo",
                    "source": source, "needs": book[r[0]]["needs"] if from_book else []})
    return {"departments": out}


@router.put("/{department_id}")
def configure(department_id: str, body: ProfileBody, user: Principal = Depends(require_admin)):
    terms = list(dict.fromkeys(t.strip() for t in body.interests if t.strip()))
    if not terms or any(len(t) > 100 for t in terms):
        raise HTTPException(422, "Provide interests of 1–100 characters.")
    with _db() as con:
        if not con.execute("SELECT 1 FROM department_profiles WHERE id=?", (department_id,)).fetchone():
            raise HTTPException(404, "Unknown department")
        con.execute("UPDATE department_profiles SET label=?,interests=?,is_demo=0 WHERE id=?",
                    (body.label, json.dumps(terms), department_id))
    store._upload_to_s3()
    return {"updated": department_id}
