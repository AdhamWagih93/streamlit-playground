from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import authenticate, current_user, make_token, upsert_user
from ..config import settings
from ..db import BadgeAward, User, get_db
from ..gamification import level_info, quest_progress

router = APIRouter(prefix="/api", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


def profile_payload(db: Session, user: User) -> dict:
    badges = db.query(BadgeAward).filter(BadgeAward.username == user.username).all()
    return {
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "streak": user.streak,
        "level": level_info(user.xp),
        "badges": [{"key": b.key, "name": b.name, "icon": b.icon} for b in badges],
        "quests": quest_progress(db, user.username),
    }


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    profile = authenticate(body.username, body.password)
    if profile is None:
        raise HTTPException(401, "invalid credentials or not in the allowed LDAP group")
    user = upsert_user(db, profile)
    return {"token": make_token(profile), "user": profile_payload(db, user),
            "demo_mode": settings.demo_mode}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return profile_payload(db, user)
