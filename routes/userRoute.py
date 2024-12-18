import base64
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import join
from starlette import status

import models.models
from classes.classes import BaseRecipe, BaseIngredient, BaseUser, SearchUsersForLocation, UpdateBaseUser
from sqlalchemy.orm import Session, joinedload
from typing import Annotated

from utils.auth import get_current_user
from utils.database import SessionLocal
from utils.util import signJWT, get_hashed_password, verify_password, create_access_token, create_refresh_token

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# annotation for dependency injection
db_dependency = Annotated[Session, Depends(get_db)]


@router.post("/user/login/", summary="Create access and refresh tokens for user")
async def login_user(db: db_dependency, form_data: OAuth2PasswordRequestForm = Depends()):
    user = db.query(models.models.User).filter(models.models.User.username == form_data.username).first()

    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username not found!")

    hashed_pass = user.password
    # if not verify_password(form_data.password, hashed_pass):
    #     raise HTTPException(
    #         status_code=status.HTTP_400_BAD_REQUEST,
    #         detail="Incorrect email or password "
    #     )

    return {
        "access_token": create_access_token(user.username),
        "refresh_token": create_refresh_token(user.username),
    }


# async def get_current_user(token: str):
#     user = fake_decode_token(token)
#     return user


@router.post("/user/create")
async def create_user(new_user: BaseUser, db: db_dependency):
    # check if the uer already exists
    user = db.query(models.models.User).filter(models.models.User.email == new_user.email).first()
    if user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
    # creating a new user
    db_user = models.models.User(**new_user.dict())
    # db_user.password = get_hashed_password(db_user.password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return (db.query(models.models.User)
            .options(joinedload(models.models.User.userType))
            .filter(models.models.User.id == db_user.id)
            .first())


@router.put("/user/update/")
async def update_user(update_user_data: UpdateBaseUser, db: db_dependency):
    # check if the user already exists
    db_user = db.query(models.models.User).filter(models.models.User.email == update_user_data.email).first()
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User does not exists")
    db_user = models.models.User(**update_user_data.dict())
    db_user = db.merge(db_user)
    db.commit()
    db.refresh(db_user)
    print('updated: ',db_user.__dict__)
    return (db.query(models.models.User)
            .options(joinedload(models.models.User.userType))
            .filter(models.models.User.id == db_user.id)
            .first())


@router.get("/users/me")
async def read_users_me(current_user: Annotated[models.models.User, Depends(get_current_user)]):
    return current_user


@router.get("/user/all")
async def get_all_users(db: db_dependency):
    return (db.query(models.models.User)
            .options(joinedload(models.models.User.userType))
            .all())


@router.get("/user/type/all")
async def get_all_user_types(db: db_dependency):
    return db.query(models.models.UserType).all()


@router.get("/user/search_by_name", summary='Filter users by a part of their name for a location')
async def get_users_by_name_and_location(db: db_dependency, searchUsersForLocation: SearchUsersForLocation = Depends()):

    # remove suggested users if they have already been assign to the location
    assigned_users = set((db.query(models.models.User)
                          .join(models.models.User.locations)
                          .filter(models.models.Location.id == searchUsersForLocation.locationID)
                          .all()))
    suggested_users = set((db.query(models.models.User)
                           .filter(models.models.User.name.ilike(f"%{searchUsersForLocation.name}%"))
                           .all()))
    left_over_users = suggested_users - assigned_users
    return left_over_users


@router.get("/user/locations/all", summary='Get all users with assigned locations')
async def get_all_users_with_locations(db: db_dependency):
    return db.query(models.models.User).options(joinedload(models.models.User.locations)).all()


@router.get("/user/location/{location_id}", summary="Get assigned Users for a Location")
async def get_users_by_location(db: db_dependency, location_id: int):
    location = db.query(models.models.Location).filter(models.models.Location.id == location_id).first()
    if location is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Location not found!")
    # Get users assigned to location
    return (db.query(models.models.User)
            .join(models.models.User.locations)
            .filter(models.models.Location.id == location_id)
            .all())


@router.put("/user/assign_location/{user_id}/{location_id}", summary="Assign location to a user")
async def assign_user_to_location(db: db_dependency, user_id: int, location_id: int):
    user = db.query(models.models.User).filter(models.models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User not found!")

    location = db.query(models.models.Location).filter(models.models.Location.id == location_id).first()
    if location is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Location not found!")

    location.users.append(user)
    db.add(location)
    db.commit()
    return await get_user_assigned_to_locations(db, user_id)


@router.delete("/user/remove_location/{user_id}/{location_id}", summary="remove user from a locationr")
async def remove_user_from_location(db: db_dependency, user_id: int, location_id: int):
    user = db.query(models.models.User).filter(models.models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User not found!")

    location = db.query(models.models.Location).filter(models.models.Location.id == location_id).first()
    if location is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Location not found!")

    location.users.remove(user)
    # Get users assigned to location
    db.add(location)
    db.commit()

    return await get_user_assigned_to_locations(db, user_id)


async def get_user_assigned_to_locations(db, user_id):
    return (db.query(models.models.User)
            .options(joinedload(models.models.User.locations))
            .filter(models.models.User.id == user_id)  # Join the locations relationship
            .all())
