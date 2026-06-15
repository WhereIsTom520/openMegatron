import os
import asyncpg
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class AuthManager:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def init_db(self):
        host = os.environ.get("PGHOST", "localhost")
        port = os.environ.get("PGPORT", "54320")
        user = os.environ.get("PGUSER", "root")
        password = os.environ.get("PGPASSWORD", "root")
        database = os.environ.get("PGDATABASE", "root")

        try:
            self.pool = await asyncpg.create_pool(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                min_size=1,
                max_size=10
            )
            logger.info("Connected to PostgreSQL for AuthManager.")
            
            # Create users table if not exists
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(255) UNIQUE NOT NULL,
                        password VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Check if root user exists, if not, create it
                root_user = await conn.fetchrow("SELECT id FROM users WHERE username = $1", "root")
                if not root_user:
                    await conn.execute(
                        "INSERT INTO users (username, password) VALUES ($1, $2)",
                        "root", "root"
                    )
                    logger.info("Created default root user.")
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL for AuthManager: {e}")

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def verify_user(self, username: str, password: str) -> bool:
        if not self.pool:
            logger.warning("AuthManager pool not initialized.")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                user = await conn.fetchrow(
                    "SELECT id FROM users WHERE username = $1 AND password = $2",
                    username, password
                )
                return user is not None
        except Exception as e:
            logger.error(f"Error verifying user: {e}")
            return False

auth_manager = AuthManager()
