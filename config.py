"""
Configuration file for DasTrader accounts
Add your account details here
One user can have multiple accounts
"""
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
        user_id="user1",
        name="User 1",
        username="USER",
        password="PASSWORD",
        host="127.0.0.1",
        port=9800,
        accounts=[
            AccountConfig(
                account_id="account1",
                name="Account 1",
                account="ACCOUNT1",
                enabled=True
            ),
            AccountConfig(
                account_id="account2",
                name="Account 2",
                account="ACCOUNT2",
                enabled=True
            ),
            # Add more accounts for this user
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

