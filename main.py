from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, case, Boolean, and_, or_
from passlib.hash import bcrypt
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED
from datetime import datetime, timedelta
import json
import os
from typing import Dict, List, Optional
import models, database
from models import Chat, Message, User, News, Comment
from fastapi import UploadFile, File
import shutil
import os
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy.orm import joinedload

# Создаем папки для хранения файлов
UPLOAD_FOLDER = "static/uploads"
AVATAR_FOLDER = "static/avatars"
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)
Path(AVATAR_FOLDER).mkdir(exist_ok=True)
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="fanqup")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

models.Base.metadata.create_all(bind=database.engine)

connections: Dict[int, List[WebSocket]] = {}

class CreateChatRequest(BaseModel):
    partner_id: int

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


def update_user_activity(db: Session, user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.last_activity = datetime.utcnow()
        db.commit()


def create_chat_if_not_exists(db: Session, user1_id: int, user2_id: int):
    chat = db.query(Chat).filter(
        or_(
            and_(Chat.user1_id == user1_id, Chat.user2_id == user2_id),
            and_(Chat.user1_id == user2_id, Chat.user2_id == user1_id)
        )
    ).first()

    if not chat:
        chat = Chat(user1_id=user1_id, user2_id=user2_id)
        db.add(chat)
        db.commit()
        db.refresh(chat)

    return chat


def format_last_seen(last_activity: datetime) -> str:
    now = datetime.utcnow()
    delta = now - last_activity

    if delta.total_seconds() < 60:
        return "только что"
    elif delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)} мин назад"
    elif delta.total_seconds() < 86400:
        return f"{int(delta.total_seconds() // 3600)} ч назад"
    else:
        return f"{int(delta.total_seconds() // 86400)} дн назад"


def get_user_chats_with_last_messages(db: Session, user_id: int):
    User1 = aliased(User)
    User2 = aliased(User)

    last_message_subquery = (
        db.query(
            Message.chat_id,
            func.max(Message.timestamp).label('last_message_time')
        )
        .group_by(Message.chat_id)
        .subquery()
    )

    unread_count_subquery = (
        db.query(
            Message.chat_id,
            func.count(Message.id).label('unread_count')
        )
        .filter(
            Message.sender_id != user_id,
            Message.is_read == False
        )
        .group_by(Message.chat_id)
        .subquery()
    )

    chats = (
        db.query(
            Chat.id.label('chat_id'),
            case(
                (Chat.user1_id == user_id, User2.username),
                (Chat.user2_id == user_id, User1.username),
                else_="Unknown"
            ).label("partner_username"),
            case(
                (Chat.user1_id == user_id, User2.id),
                (Chat.user2_id == user_id, User1.id),
            ).label("partner_id"),
            Message.content.label('last_message_content'),
            Message.timestamp.label('last_message_time'),
            func.coalesce(unread_count_subquery.c.unread_count, 0).label('unread_count')
        )
        .join(User1, Chat.user1_id == User1.id)
        .join(User2, Chat.user2_id == User2.id)
        .outerjoin(last_message_subquery, Chat.id == last_message_subquery.c.chat_id)
        .outerjoin(
            Message,
            and_(
                Message.chat_id == Chat.id,
                Message.timestamp == last_message_subquery.c.last_message_time
            )
        )
        .outerjoin(unread_count_subquery, Chat.id == unread_count_subquery.c.chat_id)
        .filter(or_(Chat.user1_id == user_id, Chat.user2_id == user_id))
        .order_by(Message.timestamp.desc())
        .all()
    )

    return chats

@app.get("/static/uploads/{chat_id}/{filename}")
async def get_chat_file(chat_id: str, filename: str):
    file_path = os.path.join(UPLOAD_FOLDER, chat_id, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="File not found")

# Добавьте эту функцию в main.py
def delete_chat_files(chat_id: int):
    chat_folder = os.path.join(UPLOAD_FOLDER, str(chat_id))
    try:
        shutil.rmtree(chat_folder)
    except FileNotFoundError:
        pass

# И используйте ее при удалении чата
@app.delete("/chat/{chat_id}")
async def delete_chat(chat_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Проверяем, что пользователь является участником чата
    if current_user.id not in [chat.user1_id, chat.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this chat")

    delete_chat_files(chat_id)
    db.delete(chat)
    db.commit()
    return {"status": "success"}

@app.post("/reg/")
async def register(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="User already exists")
    user = User(username=username, password=bcrypt.hash(password), last_activity=datetime.utcnow())
    db.add(user)
    db.commit()
    return RedirectResponse(url="/login", status_code=303)


@app.post("/login/")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = form.get("username")
    password = form.get("password")

    user = db.query(User).filter(User.username == username).first()
    if not user or not bcrypt.verify(password, user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

    update_user_activity(db, user.id)
    request.session["user_id"] = user.id
    return RedirectResponse(url="/chats", status_code=303)


@app.get("/chats", response_class=HTMLResponse)
async def chats_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    update_user_activity(db, current_user.id)
    chats = get_user_chats_with_last_messages(db, current_user.id)

    users = db.query(User).all()
    statuses = {}
    now = datetime.utcnow()

    for user in users:
        delta = now - user.last_activity
        statuses[user.username] = {
            'online': delta.total_seconds() < 300,
            'last_seen': format_last_seen(user.last_activity)
        }

    return templates.TemplateResponse("chats.html", {
        "request": request,
        "chats": chats,
        "current_user": current_user,
        "statuses": statuses
    })


# Изменим маршрут чата
@app.get("/chat/{chat_id}", response_class=HTMLResponse)
async def chat_page(
        request: Request,
        chat_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # Получаем чат и проверяем доступ
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if current_user.id not in [chat.user1_id, chat.user2_id]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Определяем собеседника
    partner = chat.user1 if chat.user2_id == current_user.id else chat.user2

    # Получаем сообщения и другую информацию
    messages = (
        db.query(Message, User)
        .join(User, Message.sender_id == User.id)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.timestamp.asc())
        .all()
    )

    # Формируем данные для шаблона
    messages_data = []
    for message, sender in messages:
        attachments = db.query(models.Attachment).filter(models.Attachment.message_id == message.id).all()
        messages_data.append({
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_username": sender.username,
            "content": message.content,
            "timestamp": message.timestamp,
            "attachments": [{
                "id": a.id,
                "file_url": a.file_url,
                "file_type": a.file_type,
                "file_name": a.file_name
            } for a in attachments]
        })

    # Получаем статусы пользователей
    users = db.query(User).all()
    statuses = {}
    now = datetime.utcnow()
    for user in users:
        delta = now - user.last_activity
        statuses[user.username] = {
            'online': delta.total_seconds() < 300,
            'last_seen': format_last_seen(user.last_activity),
            'avatar_url': user.avatar_url,
            'is_admin': user.is_admin
        }

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "chat_id": chat.id,
        "partner": partner.username,
        "partner_id": partner.id,
        "current_user": current_user,
        "current_user_id": current_user.id,
        "messages": messages_data,
        "statuses": statuses,
        "chats": get_user_chats_with_last_messages(db, current_user.id)  # Добавляем список чатов
    })


@app.get("/api/user_statuses")
async def get_user_statuses(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    users = db.query(User).all()
    statuses = {}
    now = datetime.utcnow()

    for user in users:
        delta = now - user.last_activity
        statuses[user.username] = {
            'online': delta.total_seconds() < 300,
            'last_seen': user.last_activity.isoformat()
        }

    return JSONResponse(statuses)


@app.get("/api/search_users")
async def search_users(
        query: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if len(query) < 2:
        return []

    users = db.query(User).filter(
        User.username.ilike(f"%{query}%"),
        User.id != current_user.id
    ).limit(3).all()

    return [{
        "id": user.id,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "online": (datetime.utcnow() - user.last_activity).total_seconds() < 300,
        "is_admin": user.is_admin  # Добавляем информацию об админе
    } for user in users]


@app.post("/api/create_chat")
async def create_chat(
        request_data: CreateChatRequest,  # Используем Pydantic модель
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    # Проверяем, что чат уже не существует
    existing_chat = db.query(Chat).filter(
        or_(
            and_(Chat.user1_id == current_user.id, Chat.user2_id == request_data.partner_id),
            and_(Chat.user1_id == request_data.partner_id, Chat.user2_id == current_user.id)
        )
    ).first()

    if existing_chat:
        return {"chat_id": existing_chat.id}

    # Создаем новый чат
    chat = Chat(user1_id=current_user.id, user2_id=request_data.partner_id)
    db.add(chat)
    db.commit()
    db.refresh(chat)

    return {"chat_id": chat.id}

@app.get("/api/user/{username}")
async def get_user_info(
        username: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.utcnow()
    delta = now - user.last_activity

    return {
        "username": user.username,
        "avatar_url": user.avatar_url,
        "online": delta.total_seconds() < 300,
        "last_seen": format_last_seen(user.last_activity),
        "is_admin": user.is_admin,
        "bio": user.bio
    }


@app.post("/upload_avatar")
async def upload_avatar(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    # Создаем папку для аватарок, если ее нет
    Path(AVATAR_FOLDER).mkdir(parents=True, exist_ok=True)

    file_ext = file.filename.split(".")[-1]
    filename = f"avatar_{current_user.id}.{file_ext}"
    file_path = os.path.join(AVATAR_FOLDER, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    avatar_url = f"/{AVATAR_FOLDER}/{filename}"
    current_user.avatar_url = avatar_url
    db.commit()

    return {"avatar_url": avatar_url}


@app.post("/upload_attachment/{chat_id}")
async def upload_attachment(
    chat_id: int,
    files: List[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Создаем папку для чата, если ее нет
    chat_folder = os.path.join(UPLOAD_FOLDER, str(chat_id))
    Path(chat_folder).mkdir(parents=True, exist_ok=True)

    uploaded_files = []
    for file in files:
        file_ext = file.filename.split(".")[-1]
        filename = f"file_{int(datetime.now().timestamp())}_{current_user.id}.{file_ext}"
        file_path = os.path.join(chat_folder, filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_type = "document"
        if file_ext.lower() in ["jpg", "jpeg", "png", "gif"]:
            file_type = "image"

        attachment = models.Attachment(
            file_url=f"/{UPLOAD_FOLDER}/{chat_id}/{filename}",  # Обновляем путь к файлу
            file_type=file_type,
            file_name=file.filename
        )
        db.add(attachment)
        db.commit()
        db.refresh(attachment)
        uploaded_files.append({
            "file_url": attachment.file_url,
            "file_type": attachment.file_type,
            "file_name": attachment.file_name
        })

    return uploaded_files

# Добавим новые маршруты для профилей
@app.get("/profile", response_class=HTMLResponse)
async def my_profile(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return await profile_page(request, current_user.username, db, current_user)

@app.post("/profile/update")
async def update_profile(
    request: Request,
    username: str = Form(None),
    name: str = Form(None),
    bio: str = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Проверяем, что новый username уникальный
    if username and username != current_user.username:
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already taken")

    # Обновляем данные
    if username:
        current_user.username = username
    if name:
        # Здесь можно добавить поле name в модель, если нужно
        pass
    if bio is not None:
        current_user.bio = bio

    db.commit()
    return RedirectResponse(url="/profile", status_code=303)

@app.get("/profile/{username}", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    username: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.utcnow()
    delta = now - user.last_activity

    status = {
        "online": delta.total_seconds() < 300,
        "last_seen": format_last_seen(user.last_activity)
    }

    is_own_profile = user.id == current_user.id

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "status": status,
        "is_own_profile": is_own_profile
    })

@app.websocket("/ws/chat/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: int, db: Session = Depends(get_db)):
    await websocket.accept()
    user_id = websocket.query_params.get("user_id")

    if user_id:
        update_user_activity(db, int(user_id))

    if chat_id not in connections:
        connections[chat_id] = []
    connections[chat_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            data_json = json.loads(data)
            sender_id = data_json.get("sender_id")
            content = data_json.get("content")
            action = data_json.get("action")

            if sender_id:
                update_user_activity(db, int(sender_id))

            if action == "get_chats":
                chats = get_user_chats_with_last_messages(db, int(sender_id))
                chats_data = [{
                    "chat_id": chat.chat_id,
                    "partner_username": chat.partner_username,
                    "last_message_content": chat.last_message_content,
                    "unread_count": chat.unread_count,
                    "partner_id": chat.partner_id
                } for chat in chats]
                await websocket.send_text(json.dumps({"action": "update_chats", "chats": chats_data}))
                continue

            if action == "send_message":
                # Создаем сообщение
                message = Message(
                    chat_id=chat_id,
                    sender_id=int(sender_id),
                    content=content,
                    is_read=False
                )
                db.add(message)
                db.commit()
                db.refresh(message)


                # Добавляем вложения если есть
                attachments_data = data_json.get("attachments", [])
                for attachment in attachments_data:
                    db_attachment = models.Attachment(
                        message_id=message.id,
                        file_url=attachment["file_url"],
                        file_type=attachment["file_type"],
                        file_name=attachment["file_name"]
                    )
                    db.add(db_attachment)
                db.commit()

                # Формируем ответ
                response_data = {
                    "action": "new_message",
                    "sender_id": int(sender_id),
                    "content": content,
                    "timestamp": message.timestamp.isoformat(),
                    "chat_id": chat_id,
                    "is_current_chat": False,
                    "attachments": attachments_data,
                    "for_push": True
                }

                # Отправляем сообщение всем участникам чата
                for conn in connections[chat_id]:
                    # Проверяем, является ли это текущим чатом для соединения
                    if hasattr(conn, 'query_params') and 'current_chat_id' in conn.query_params and int(conn.query_params['current_chat_id']) == chat_id:
                        response_data["is_current_chat"] = True
                        # Помечаем сообщение как прочитанное только для текущего чата
                        message.is_read = True
                        db.commit()

                    await conn.send_text(json.dumps(response_data))

                    # Обновляем список чатов для всех подключений
                    chats = get_user_chats_with_last_messages(db, int(sender_id))
                    chats_data = [{
                        "chat_id": chat.chat_id,
                        "partner_username": chat.partner_username,
                        "last_message_content": chat.last_message_content,
                        "unread_count": chat.unread_count,
                        "partner_id": chat.partner_id
                    } for chat in chats]
                    await conn.send_text(json.dumps({"action": "update_chats", "chats": chats_data}))

    except WebSocketDisconnect:
        if chat_id in connections:
            connections[chat_id].remove(websocket)
            if not connections[chat_id]:
                del connections[chat_id]


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/sw.js")
async def get_service_worker():
    return FileResponse("sw.js", media_type="application/javascript")


# Добавим в main.py новые маршруты для админки

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(
        request: Request,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    users = db.query(User).all()
    chats = db.query(Chat).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "users": users,
        "chats": chats,
        "current_user": current_user,
        "format_last_seen": format_last_seen,  # Передаем функцию
        "datetime": datetime  # Также передаем datetime
    })


@app.post("/admin/update_user/{user_id}")
async def admin_update_user(
        user_id: int,
        is_admin: bool = Form(False),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_admin = is_admin
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_user/{user_id}")
async def admin_delete_user(
        user_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Удаляем все чаты пользователя
    chats = db.query(Chat).filter(
        or_(Chat.user1_id == user_id, Chat.user2_id == user_id)
    ).all()

    for chat in chats:
        delete_chat_files(chat.id)
        db.delete(chat)

    # Удаляем пользователя
    db.delete(user)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_chat/{chat_id}")
async def admin_delete_chat(
        chat_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    delete_chat_files(chat_id)
    db.delete(chat)
    db.commit()

    return RedirectResponse(url="/admin", status_code=303)


# Добавим в начало файла
NEWS_IMAGE_FOLDER = "static/news_images"
Path(NEWS_IMAGE_FOLDER).mkdir(exist_ok=True)


# Маршруты для новостей
# Новые маршруты должны быть в таком порядке:

@app.get("/news/add", response_class=HTMLResponse)
async def add_news_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can add news")
    return templates.TemplateResponse("news/add.html", {"request": request})


@app.get("/news/{news_id}", response_class=HTMLResponse)
async def news_detail(
        request: Request,
        news_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    news_item = db.query(News).filter(News.id == news_id).first()
    if not news_item:
        raise HTTPException(status_code=404, detail="News not found")

    # Загружаем комментарии с авторами и ответами
    news_item.comments = (
        db.query(Comment)
        .filter(Comment.news_id == news_id)
        .options(joinedload(Comment.author))
        .order_by(Comment.created_at.asc())
        .all()
    )

    return templates.TemplateResponse("news/detail.html", {
        "request": request,
        "news": news_item,
        "current_user": current_user
    })

@app.get("/news", response_class=HTMLResponse)
async def news_list(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    news_items = db.query(News).filter(News.is_published == True).order_by(News.created_at.desc()).all()
    return templates.TemplateResponse("news/list.html", {
        "request": request,
        "news": news_items,
        "current_user": current_user
    })

@app.post("/news/add")
async def add_news(
        request: Request,
        title: str = Form(...),
        content: str = Form(...),
        image: UploadFile = File(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can add news")

    image_url = None
    if image and image.filename:
        file_ext = image.filename.split(".")[-1]
        filename = f"news_{int(datetime.now().timestamp())}.{file_ext}"
        file_path = os.path.join(NEWS_IMAGE_FOLDER, filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

        image_url = f"/{NEWS_IMAGE_FOLDER}/{filename}"

    news = News(
        title=title,
        content=content,
        author_id=current_user.id,
        image_url=image_url
    )
    db.add(news)
    db.commit()

    return RedirectResponse(url="/news", status_code=303)


# Добавим новые маршруты для комментариев
@app.post("/news/{news_id}/comments")
async def add_comment(
        request: Request,
        news_id: int,
        content: str = Form(...),
        parent_id: Optional[int] = Form(None),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    news = db.query(News).filter(News.id == news_id).first()
    if not news:
        raise HTTPException(status_code=404, detail="News not found")

    comment = Comment(
        content=content,
        author_id=current_user.id,
        news_id=news_id,
        parent_id=parent_id
    )
    db.add(comment)
    db.commit()

    return RedirectResponse(url=f"/news/{news_id}#comments", status_code=303)


@app.post("/comments/{comment_id}/delete")
async def delete_comment(
        comment_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # Проверяем права: автор или админ
    if comment.author_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    db.delete(comment)
    db.commit()

    return RedirectResponse(url=f"/news/{comment.news_id}#comments", status_code=303)