# DasTrader Dashboard

A real-time trading dashboard for monitoring multiple DasTrader accounts. Built with FastAPI backend and Next.js frontend.

## Features

- **Live Trade View**: Real-time display of open positions, entry prices, mark prices, P&L
- **Account Overview**: Complete account summary including equity, margin usage, buying power
- **Activity Log**: Chronological history of trades, orders, and system events
- **Multi-Account Support**: Monitor multiple DasTrader accounts per user
- **Real-time Updates**: WebSocket-based live data streaming
- **Mobile-Friendly**: Responsive design for mobile and desktop

## Architecture

- **Backend**: FastAPI with WebSocket support for real-time updates
- **Frontend**: Next.js 14 with TypeScript and Tailwind CSS
- **Connection**: Socket-based connection to DasTrader CMD API

## Configuration Structure

The configuration follows a user-centric model where:
- **Users** have connection details (host, port, username, password)
- **Accounts** belong to users and share the same connection
- One user can have multiple accounts on the same DasTrader instance

### Example Configuration

```python
USERS = [
    UserConfig(
        user_id="user1",
        name="User 1",
        username="USER",
        password="PASSWORD",
        host="127.0.0.1",  # DasTrader instance IP
        port=9800,          # DasTrader CMD API port
        accounts=[
            AccountConfig(
                account_id="account1",
                name="Account 1",
                account="ACCOUNT1",  # DasTrader account identifier
                enabled=True
            ),
            AccountConfig(
                account_id="account2",
                name="Account 2",
                account="ACCOUNT2",
                enabled=True
            ),
        ]
    ),
]
```

## Setup

### Backend Setup

1. Navigate to the Backend directory:
```bash
cd Backend
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Configure your users and accounts in `Backend/config.py`:
   - Add `UserConfig` entries with host, port, username, password
   - Add `AccountConfig` entries under each user

4. Start the FastAPI server:
```bash
python main.py
```

The backend will run on `http://localhost:8000`

### Frontend Setup

1. Navigate to the Frontend directory:
```bash
cd Frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

The frontend will run on `http://localhost:3000`

## Usage

1. Make sure DasTrader is running on the configured IP addresses and ports
2. Start the backend server
3. Start the frontend server
4. Open your browser to `http://localhost:3000`
5. View accounts grouped by user, then select an account to view its dashboard

## API Endpoints

### REST API

- `GET /api/accounts` - List all configured accounts grouped by user
- `GET /api/accounts/{account_id}/positions` - Get positions for an account
- `GET /api/accounts/{account_id}/orders` - Get orders for an account
- `GET /api/accounts/{account_id}/trades` - Get trades for an account
- `GET /api/accounts/{account_id}/overview` - Get account overview
- `GET /api/accounts/{account_id}/activity` - Get activity log
- `POST /api/accounts/{account_id}/refresh` - Manually refresh account data

### WebSocket

- `ws://localhost:8000/ws` - Real-time updates for positions, orders, trades, and account info

## Dashboard Pages

### Live Trade View
- Current open positions with entry and mark prices
- Unrealized and realized P&L
- Open orders with status

### Account Overview
- Total account equity
- Margin usage
- Cash balance and buying power
- Realized and unrealized P&L
- Commission and fees

### Activity Log
- Executed trades
- Order actions (accept, cancel, reject, etc.)
- System-generated notes
- Error messages

## Configuration Details

### User Configuration
Each user requires:
- `user_id`: Unique identifier
- `name`: Display name
- `username`: DasTrader username
- `password`: DasTrader password
- `host`: IP address of DasTrader instance
- `port`: CMD API port (default: 9800)
- `accounts`: List of accounts for this user

### Account Configuration
Each account requires:
- `account_id`: Unique identifier
- `name`: Display name
- `account`: DasTrader account identifier
- `enabled`: Whether the account is active

## Notes

- The backend automatically connects to all enabled accounts on startup
- All accounts under a user share the same connection (host/port/credentials)
- Data is refreshed every 5 seconds via periodic polling
- WebSocket provides real-time updates for immediate changes
- Position unrealized P&L is calculated using live quote data
- Activity log keeps the last 1000 entries per account

## Troubleshooting

1. **Connection Issues**: Ensure DasTrader is running and CMD API is enabled
2. **No Data**: Check that account credentials are correct in `config.py`
3. **WebSocket Disconnected**: The frontend will automatically reconnect
4. **Port Conflicts**: Change ports in `main.py` (backend) or `package.json` scripts (frontend)

## License

This project is provided as-is for use with DasTrader.
