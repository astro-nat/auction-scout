"""The never list - kinds of thing to hide, editable without a deploy.

The inverse of the BOLO list. BOLO lives in data files because the user
kept thinking of brands worth flagging; this lives in the database because
the user keeps thinking of things not worth seeing, and waiting for a push
each time is the problem it exists to solve.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import never

router = APIRouter(prefix="/never", tags=["never"])


class NeverRuleIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    phrases: List[str] = Field(default_factory=list)
    except_phrases: List[str] = Field(default_factory=list)
    enabled: bool = True


class NeverRulePatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phrases: Optional[List[str]] = None
    except_phrases: Optional[List[str]] = None
    enabled: Optional[bool] = None


class NeverRuleOut(BaseModel):
    id: int
    label: str
    phrases: List[str]
    except_phrases: List[str] = []
    enabled: bool
    seeded: bool

    @classmethod
    def of(cls, r: models.NeverRule) -> "NeverRuleOut":
        return cls(id=r.id, label=r.label, phrases=list(r.phrases or []),
                   except_phrases=list(r.except_phrases or []),
                   enabled=bool(r.enabled), seeded=bool(r.seeded))


def rules_for_matching(db: Session) -> list:
    """Enabled rules, oldest first - the order decides which label a lot
    showing two kinds of unwanted thing reports."""
    return (db.query(models.NeverRule)
              .filter(models.NeverRule.enabled.is_(True))
              .order_by(models.NeverRule.id)
              .all())


def ensure_seeded(db: Session) -> int:
    """Install the shipped rules, but only into an empty list.

    Per-label seeding was the obvious version and it was wrong: removing
    "Vacuum cleaners" would put it straight back on the next page load, and
    the user would have no way to get rid of it short of a code change -
    which is the thing this feature exists to avoid. Once there is anything
    in the table the list is the user's; emptying it entirely is what asks
    for the shipped three back.
    """
    if db.query(models.NeverRule.id).first() is not None:
        return 0
    for s in never.SEEDS:
        db.add(models.NeverRule(label=s["label"], phrases=s["phrases"],
                                except_phrases=s.get("except_phrases") or [],
                                enabled=True, seeded=True))
    db.commit()
    return len(never.SEEDS)


@router.get("", response_model=List[NeverRuleOut])
def list_rules(db: Session = Depends(get_db)):
    ensure_seeded(db)
    rows = db.query(models.NeverRule).order_by(models.NeverRule.id).all()
    return [NeverRuleOut.of(r) for r in rows]


@router.post("", response_model=NeverRuleOut, status_code=201)
def add_rule(payload: NeverRuleIn, db: Session = Depends(get_db)):
    phrases = [p.strip() for p in payload.phrases if p and p.strip()]
    if not phrases:
        raise HTTPException(status_code=422,
                            detail="A rule needs at least one phrase to match")
    row = models.NeverRule(
        label=payload.label.strip(), phrases=phrases,
        except_phrases=[p.strip() for p in payload.except_phrases if p and p.strip()],
        enabled=payload.enabled, seeded=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return NeverRuleOut.of(row)


@router.patch("/{rule_id}", response_model=NeverRuleOut)
def edit_rule(rule_id: int, payload: NeverRulePatch, db: Session = Depends(get_db)):
    row = db.query(models.NeverRule).filter(models.NeverRule.id == rule_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="No such rule")
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.phrases is not None:
        kept = [p.strip() for p in payload.phrases if p and p.strip()]
        if not kept:
            raise HTTPException(status_code=422,
                                detail="A rule needs at least one phrase to match")
        row.phrases = kept
    if payload.except_phrases is not None:
        row.except_phrases = [p.strip() for p in payload.except_phrases if p and p.strip()]
    if payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return NeverRuleOut.of(row)


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    """Deleting a seeded rule is allowed - it is the user's list. A seed
    comes back only if no rule with its label is left at all.

    Returns a body rather than a bare 204 because the client's fetch helper
    parses every response as JSON.
    """
    gone = (db.query(models.NeverRule)
              .filter(models.NeverRule.id == rule_id)
              .delete(synchronize_session=False))
    db.commit()
    return {"deleted": int(gone)}


@router.post("/preview")
def preview(payload: NeverRuleIn, limit: int = 40, db: Session = Depends(get_db)):
    """What this rule would hide, before saving it.

    Writing a phrase list without seeing what it catches is how "blade"
    hides Blade Runner. Read-only.
    """
    phrases = [p.strip() for p in payload.phrases if p and p.strip()]
    if not phrases:
        return {"matched": 0, "titles": []}
    excepts = [p.strip() for p in payload.except_phrases if p and p.strip()]
    rows = db.query(models.Lot.title).all()
    hits = [t for (t,) in rows if never.rule_matches(t, phrases, excepts)]
    return {"matched": len(hits), "titles": hits[:limit]}
