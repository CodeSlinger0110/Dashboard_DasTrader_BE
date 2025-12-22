"""
Configuration file for DasTrader accounts
Add your account details here
One user can have multiple accounts
"""
from tkinter import FALSE
from typing import List, Dict
from pydantic import BaseModel

class AccountConfig(BaseModel):
    account_id: str
    name: str
    account: str  # DasTrader account identifier
    enabled: bool = True

class UserConfig(BaseModel):
    user_id: str
    name: str
    username: str  # DasTrader username
    password: str  # DasTrader password
    host: str
    port: int
    accounts: List[AccountConfig]

# Configure users and their accounts here
# Each user can have multiple accounts
USERS: List[UserConfig] = [
    UserConfig(
        user_id="CB4507",
        name="CB4507",
        username="CB4507",
        password="DSJ23100M!",
        host="155.94.150.127",
        port=9805,
        accounts=[
            AccountConfig(
                account_id="TR4507",
                name="TR4507",
                account="TR4507",
                enabled=True
            ),
            AccountConfig(
                account_id="1RB13972",
                name="1RB13972",
                account="1RB13972",
                enabled=True
            ),
        ]
    ),
    UserConfig(
        user_id="Cb4939",
        name="Cb4939",
        username="Cb4939",
        password="Hayden2025@",
        host="155.94.150.127",
        port=9803,
        accounts=[
            AccountConfig(
                account_id="TR4939",
                name="TR4939",
                account="TR4939",
                enabled=True
            ),
            AccountConfig(
                account_id="1RB14079",
                name="1RB14079",
                account="1RB14079",
                enabled=True
            ),
        ]
    ),
    UserConfig(
        user_id="CB4960",
        name="CB4960",
        username="CB4960",
        password="Hayden2026@",
        host="155.94.150.127",
        port=9810,
        accounts=[
            AccountConfig(
                account_id="1RB14089",
                name="1RB14089",
                account="1RB14089",
                enabled=True
            ),
        ]
    ),
    UserConfig(
        user_id="CB4954",
        name="CB4954",
        username="CB4954",
        password="Hayden2025@",
        host="155.94.150.127",
        port=9801,
        accounts=[
            AccountConfig(
                account_id="TR4954",
                name="TR4954",
                account="TR4954",
                enabled=True
            ),
        ]
    ),
]
# Flatten accounts list for backward compatibility and easier access
# Format: {account_id: (user_config, account_config)}
ACCOUNTS_DICT: Dict[str, tuple] = {}

# Helper class for flattened account view (includes host/port from user)
class FlattenedAccount:
    def __init__(self, account: AccountConfig, user: UserConfig):
        self.account_id = account.account_id
        self.name = account.name
        self.account = account.account
        self.enabled = account.enabled
        self.host = user.host
        self.port = user.port
        self.user_id = user.user_id
        self.user_name = user.name
        self.username = user.username
        self.password = user.password

ACCOUNTS: List[FlattenedAccount] = []

for user in USERS:
    for account in user.accounts:
        ACCOUNTS_DICT[account.account_id] = (user, account)
        # Create a flattened account with user credentials and connection info
        flat_account = FlattenedAccount(account, user)
        ACCOUNTS.append(flat_account)

# Authentication credentials (fixed for now)
# In production, these should be stored securely (environment variables, secrets manager, etc.)
AUTH_CREDENTIALS = {
    "admin": "admin123",  # username: password
    "user": "password123",
}

# JWT Secret key (in production, use a secure random key from environment variable)
JWT_SECRET_KEY = "your-secret-key-change-this-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24



