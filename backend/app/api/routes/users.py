from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.api.deps import AdminUser, SessionDep
from app.core.security import hash_password
from app.models import AppUser
from app.schemas.api import Message, UserCreate, UserOut, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(_: AdminUser, session: SessionDep):
    return session.exec(select(AppUser).order_by(AppUser.username)).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, _: AdminUser, session: SessionDep):
    try:
        return user_service.create_user(session, body.username, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: AdminUser, session: SessionDep):
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    demoting = (body.role == "user" or body.is_active is False) and user.role == "admin"
    if demoting and user_service.count_admins(session) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot remove the last active admin")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password:
        user.hashed_password = hash_password(body.password)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.delete("/{user_id}", response_model=Message)
def delete_user(user_id: int, admin: AdminUser, session: SessionDep):
    user = session.get(AppUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if user.role == "admin" and user_service.count_admins(session) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete the last active admin")
    session.delete(user)
    session.commit()
    return Message(message=f"Deleted user {user.username}")
