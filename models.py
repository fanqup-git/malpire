from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Boolean  # Добавили Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    avatar_url = Column(String, default="/static/default_avatar.png")  # Добавляем поле для аватарки
    last_activity = Column(DateTime, default=datetime.utcnow)
    bio = Column(String, default="")  # Добавляем поле для био
    is_admin = Column(Boolean, default=False)  # Новое поле
    messages_sent = relationship("Message", foreign_keys="Message.sender_id", back_populates="sender")
    chats_user1 = relationship("Chat", foreign_keys="Chat.user1_id", back_populates="user1")
    chats_user2 = relationship("Chat", foreign_keys="Chat.user2_id", back_populates="user2")

class Attachment(Base):
    __tablename__ = "attachments"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"))
    file_url = Column(String)
    file_type = Column(String)  # 'image', 'document', etc.
    file_name = Column(String)
    upload_date = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    sender_id = Column(Integer, ForeignKey("users.id"))
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)
    attachments = relationship("Attachment", backref="message", cascade="all, delete-orphan")
    chat = relationship("Chat", back_populates="messages")
    sender = relationship("User", foreign_keys=[sender_id], back_populates="messages_sent")

class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    user1_id = Column(Integer, ForeignKey("users.id"))
    user2_id = Column(Integer, ForeignKey("users.id"))
    messages = relationship("Message", back_populates="chat")
    user1 = relationship("User", foreign_keys=[user1_id], back_populates="chats_user1")
    user2 = relationship("User", foreign_keys=[user2_id], back_populates="chats_user2")

class News(Base):
    __tablename__ = "news"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_published = Column(Boolean, default=True)
    image_url = Column(String, nullable=True)

    author = relationship("User", backref="news")


class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"))
    news_id = Column(Integer, ForeignKey("news.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True)  # Для ответов на комментарии

    author = relationship("User", backref="comments")
    news = relationship("News", backref="comments")
    parent = relationship("Comment", remote_side=[id], backref="replies")