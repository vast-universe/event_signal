"""
数据库连接管理
"""
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from ..config import DATABASE_URL

Base = declarative_base()


class Database:
    """数据库管理类"""

    def __init__(self, url: str = DATABASE_URL):
        if not url:
            raise ValueError("DATABASE_URL 环境变量未设置")
        self.url = url
        self.engine = None
        self.session_factory = None

    async def connect(self):
        """连接数据库"""
        self.engine = create_async_engine(self.url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        print(f"✅ 数据库已连接: {self.url}")

    async def disconnect(self):
        """断开连接"""
        if self.engine:
            await self.engine.dispose()
            print("数据库已断开")

    @asynccontextmanager
    async def session(self):
        """获取数据库会话"""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


_db: Optional[Database] = None


async def get_db() -> Database:
    """获取数据库实例"""
    global _db
    if _db is None:
        _db = Database()
        await _db.connect()
    return _db
