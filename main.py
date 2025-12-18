"""
FastAPI Backend for DasTrader Dashboard
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Dict, Optional
from contextlib import asynccontextmanager
import asyncio
import json
import logging
from datetime import datetime, timedelta
from jose import JWTError, jwt

try:
    from .config import ACCOUNTS, ACCOUNTS_DICT, USERS, AUTH_CREDENTIALS, JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
    from .das_connection import ConnectionManager, DasConnection
    from .data_parser import DataParser
except ImportError:
    from config import ACCOUNTS, ACCOUNTS_DICT, USERS, AUTH_CREDENTIALS, JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRATION_HOURS
    from das_connection import ConnectionManager, DasConnection
    from data_parser import DataParser

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global connection manager
connection_manager = ConnectionManager()
data_parser = DataParser()

# Store latest data for each account
account_data: Dict[str, Dict] = {}
websocket_connections: List[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("Starting up...")
    # Initialize connections from config
    # Each account gets credentials and connection info from its parent user
    for account_id, (user, account) in ACCOUNTS_DICT.items():
        if account.enabled:
            conn = connection_manager.add_connection(
                account.account_id,
                user.host,      # Use user's host
                user.port,       # Use user's port
                user.username,   # Use user's username
                user.password,   # Use user's password
                account.account  # Use account identifier
            )
            # Register callbacks
            conn.register_callback("position", handle_position_update)
            conn.register_callback("order", handle_order_update)
            conn.register_callback("trade", handle_trade_update)
            conn.register_callback("account", handle_account_update)
            conn.register_callback("quote", handle_quote_update)
            
            # Initialize account data
            account_data[account.account_id] = {
                "positions": [],
                "orders": [],
                "trades": [],
                "account_info": None,
                "buying_power": None,
                "quotes": {},
                "last_update": None,
                "user_id": user.user_id,
                "user_name": user.name,
                "host": user.host,
                "port": user.port
            }
    
    # Connect to all accounts
    await connection_manager.connect_all()
    
    # Start background update tasks
    asyncio.create_task(periodic_updates())
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await connection_manager.disconnect_all()


app = FastAPI(title="DasTrader Dashboard API", lifespan=lifespan)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authentication
security = HTTPBearer()

# Pydantic models for authentication
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception


async def handle_position_update(account_id: str, data: str):
    """Handle position update"""
    pos = data_parser._parse_position_line(data)
    if pos and account_id in account_data:
        # Update position in account data
        positions = account_data[account_id]["positions"]
        # Find and update existing position or add new
        found = False
        for i, p in enumerate(positions):
            if p["symbol"] == pos["symbol"]:
                positions[i] = pos
                found = True
                break
        if not found:
            positions.append(pos)
        account_data[account_id]["last_update"] = datetime.now().isoformat()
        await broadcast_update(account_id, "position", pos)


async def handle_order_update(account_id: str, data: str):
    """Handle order update"""
    if data.startswith("%ORDER"):
        order = data_parser._parse_order_line(data)
        if order and account_id in account_data:
            orders = account_data[account_id]["orders"]
            # Update or add order
            found = False
            for i, o in enumerate(orders):
                if o["order_id"] == order["order_id"]:
                    orders[i] = order
                    found = True
                    break
            if not found:
                orders.append(order)
            account_data[account_id]["last_update"] = datetime.now().isoformat()
            await broadcast_update(account_id, "order", order)
    elif data.startswith("%OrderAct"):
        action = data_parser.parse_order_action(data)
        if action:
            await broadcast_update(account_id, "order_action", action)


async def handle_trade_update(account_id: str, data: str):
    """Handle trade update"""
    trade = data_parser._parse_trade_line(data)
    if trade and account_id in account_data:
        account_data[account_id]["trades"].insert(0, trade)  # Add to beginning
        # Keep only last 1000 trades
        if len(account_data[account_id]["trades"]) > 1000:
            account_data[account_id]["trades"] = account_data[account_id]["trades"][:1000]
        account_data[account_id]["last_update"] = datetime.now().isoformat()
        await broadcast_update(account_id, "trade", trade)


async def handle_account_update(account_id: str, data: str):
    """Handle account info update"""
    if data.startswith("$AccountInfo"):
        info = data_parser.parse_account_info(data)
        if info and account_id in account_data:
            account_data[account_id]["account_info"] = info
            account_data[account_id]["last_update"] = datetime.now().isoformat()
            await broadcast_update(account_id, "account_info", info)
    elif data.startswith("BP"):
        bp = data_parser.parse_buying_power(data)
        if bp and account_id in account_data:
            account_data[account_id]["buying_power"] = bp
            await broadcast_update(account_id, "buying_power", bp)


async def handle_quote_update(account_id: str, data: str):
    """Handle quote update"""
    quote = data_parser.parse_quote(data)
    if quote and account_id in account_data:
        symbol = quote.get("symbol")
        if symbol:
            account_data[account_id]["quotes"][symbol] = quote
            await broadcast_update(account_id, "quote", quote)


async def broadcast_update(account_id: str, update_type: str, data: Dict):
    """Broadcast update to all WebSocket connections"""
    message = {
        "type": update_type,
        "account_id": account_id,
        "data": data,
        "timestamp": datetime.now().isoformat()
    }
    
    disconnected = []
    for ws in websocket_connections:
        try:
            await ws.send_json(message)
        except:
            disconnected.append(ws)
    
    # Remove disconnected websockets
    for ws in disconnected:
        if ws in websocket_connections:
            websocket_connections.remove(ws)


async def update_account_data(account_id: str, conn: DasConnection):
    """Update data for a single account"""
    if not conn.connected:
        return
    
    try:
        # Run all commands in parallel for faster updates
        pos_task = conn.send_command("GET POSITIONS")
        order_task = conn.send_command("GET ORDERS")
        acc_task = conn.send_command("GET AccountInfo")
        bp_task = conn.send_command("GET BP")
        
        # Wait for all commands to complete
        pos_data, order_data, acc_data, bp_data = await asyncio.gather(
            pos_task, order_task, acc_task, bp_task,
            return_exceptions=True
        )
        
        # Process results
        if account_id in account_data:
            if pos_data and not isinstance(pos_data, Exception):
                positions = data_parser.parse_positions(pos_data)
                account_data[account_id]["positions"] = positions
            
            if order_data and not isinstance(order_data, Exception):
                orders = data_parser.parse_orders(order_data)
                account_data[account_id]["orders"] = orders
            
            if acc_data and not isinstance(acc_data, Exception):
                info = data_parser.parse_account_info(acc_data)
                if info:
                    account_data[account_id]["account_info"] = info
            
            if bp_data and not isinstance(bp_data, Exception):
                bp = data_parser.parse_buying_power(bp_data)
                if bp:
                    account_data[account_id]["buying_power"] = bp
            
            account_data[account_id]["last_update"] = datetime.now().isoformat()
    except Exception as e:
        logger.error(f"Error updating {account_id}: {e}")


async def periodic_updates():
    """Periodic updates for positions, orders, account info"""
    while True:
        try:
            # Update all accounts in parallel for better performance
            update_tasks = []
            for account_id, conn in connection_manager.get_all_connections().items():
                if conn.connected:
                    update_tasks.append(update_account_data(account_id, conn))
            
            # Wait for all updates to complete
            if update_tasks:
                await asyncio.gather(*update_tasks, return_exceptions=True)
            
            await asyncio.sleep(5)  # Update every 5 seconds
        except Exception as e:
            logger.error(f"Error in periodic updates: {e}")
            await asyncio.sleep(5)


# REST API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "DasTrader Dashboard API", "status": "running"}

# Authentication endpoints (public)
@app.post("/api/auth/login", response_model=TokenResponse)
async def login(login_data: LoginRequest):
    """Login endpoint - returns JWT token"""
    username = login_data.username
    password = login_data.password
    
    # Check credentials
    if username not in AUTH_CREDENTIALS or AUTH_CREDENTIALS[username] != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token = create_access_token(data={"sub": username})
    return TokenResponse(access_token=access_token)

@app.get("/api/auth/verify")
async def verify_auth(current_user: str = Depends(verify_token)):
    """Verify token endpoint"""
    return {"username": current_user, "valid": True}

@app.get("/api/accounts")
async def get_accounts(current_user: str = Depends(verify_token)):
    """Get list of configured accounts grouped by user"""
    result = {
        "users": [],
        "accounts": []
    }
    
    # Group accounts by user
    for user in USERS:
        user_accounts = []
        for account in user.accounts:
            if account.enabled:
                conn = connection_manager.get_connection(account.account_id)
                account_info = {
                    "account_id": account.account_id,
                    "name": account.name,
                    "host": user.host,      # Host from user
                    "port": user.port,      # Port from user
                    "user_id": user.user_id,
                    "user_name": user.name,
                    "connected": conn.connected if conn else False
                }
                user_accounts.append(account_info)
                result["accounts"].append(account_info)
        
        if user_accounts:
            result["users"].append({
                "user_id": user.user_id,
                "name": user.name,
                "host": user.host,
                "port": user.port,
                "accounts": user_accounts
            })
    
    return result


@app.get("/api/accounts/{account_id}/positions")
async def get_positions(account_id: str, current_user: str = Depends(verify_token)):
    """Get positions for an account"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Calculate current mark price and unrealized PnL from quotes
    positions = account_data[account_id]["positions"].copy()
    quotes = account_data[account_id]["quotes"]
    
    for pos in positions:
        symbol = pos["symbol"]
        if symbol in quotes:
            quote = quotes[symbol]
            mark_price = quote.get("l", pos["avg_cost"])  # Use last price as mark
            pos["mark_price"] = mark_price
            # Recalculate unrealized PnL
            if pos["type"] == "short":
                pos["unrealized_pnl"] = (pos["avg_cost"] - mark_price) * pos["quantity"]
            else:
                pos["unrealized_pnl"] = (mark_price - pos["avg_cost"]) * pos["quantity"]
    
    return {"account_id": account_id, "positions": positions}


@app.get("/api/accounts/{account_id}/orders")
async def get_orders(account_id: str, current_user: str = Depends(verify_token)):
    """Get orders for an account"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"account_id": account_id, "orders": account_data[account_id]["orders"]}


@app.get("/api/accounts/{account_id}/trades")
async def get_trades(account_id: str, limit: int = 100, current_user: str = Depends(verify_token)):
    """Get recent trades for an account"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    trades = account_data[account_id]["trades"][:limit]
    return {"account_id": account_id, "trades": trades}


@app.get("/api/accounts/{account_id}/overview")
async def get_account_overview(account_id: str, current_user: str = Depends(verify_token)):
    """Get account overview (equity, margin, cash, etc.)"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    
    data = account_data[account_id]
    account_info = data.get("account_info", {})
    buying_power = data.get("buying_power", {})
    positions = data.get("positions", [])
    
    # Calculate total unrealized PnL
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    
    # Calculate exposure by asset class (simplified - all equities for now)
    equity_exposure = sum(abs(p["quantity"] * p.get("mark_price", p["avg_cost"])) for p in positions)
    
    return {
        "account_id": account_id,
        "user_id": data.get("user_id"),
        "user_name": data.get("user_name"),
        "current_equity": account_info.get("current_equity", 0),
        "open_equity": account_info.get("open_equity", 0),
        "realized_pl": account_info.get("realized_pl", 0),
        "unrealized_pl": total_unrealized,
        "net_pl": account_info.get("net_pl", 0),
        "buying_power": buying_power.get("current_bp", 0),
        "overnight_bp": buying_power.get("overnight_bp", 0),
        "equity_exposure": equity_exposure,
        "commission": account_info.get("commission", 0),
        "fees": account_info.get("sec_fee", 0) + account_info.get("finra_fee", 0) + account_info.get("ecn_fee", 0),
        "last_update": data.get("last_update")
    }


@app.get("/api/accounts/{account_id}/activity")
async def get_activity(account_id: str, limit: int = 100, current_user: str = Depends(verify_token)):
    """Get activity log (trades, order actions, etc.)"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    
    activities = []
    
    # Add trades
    for trade in account_data[account_id]["trades"][:limit]:
        activities.append({
            "type": "trade",
            "timestamp": trade.get("time", ""),
            "symbol": trade["symbol"],
            "side": trade["side"],
            "quantity": trade["quantity"],
            "price": trade["price"],
            "realized_pl": trade.get("realized_pl", 0),
            "data": trade
        })
    
    # Sort by timestamp (most recent first)
    activities.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return {"account_id": account_id, "activities": activities[:limit]}


@app.post("/api/accounts/{account_id}/refresh")
async def refresh_account_data(account_id: str, current_user: str = Depends(verify_token)):
    """Manually refresh account data"""
    conn = connection_manager.get_connection(account_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not conn.connected:
        raise HTTPException(status_code=400, detail="Account not connected")
    
    try:
        # Refresh all data
        pos_data = await conn.send_command("GET POSITIONS")
        order_data = await conn.send_command("GET ORDERS")
        trade_data = await conn.send_command("GET TRADES")
        acc_data = await conn.send_command("GET AccountInfo")
        bp_data = await conn.send_command("GET BP")
        
        if account_id in account_data:
            if pos_data:
                account_data[account_id]["positions"] = data_parser.parse_positions(pos_data)
            if order_data:
                account_data[account_id]["orders"] = data_parser.parse_orders(order_data)
            if trade_data:
                account_data[account_id]["trades"] = data_parser.parse_trades(trade_data)
            if acc_data:
                account_data[account_id]["account_info"] = data_parser.parse_account_info(acc_data)
            if bp_data:
                account_data[account_id]["buying_power"] = data_parser.parse_buying_power(bp_data)
            account_data[account_id]["last_update"] = datetime.now().isoformat()
        
        return {"status": "success", "message": "Data refreshed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/accounts/{account_id}/reconnect")
async def reconnect_account(account_id: str, current_user: str = Depends(verify_token)):
    """Retry connection for a specific account"""
    conn = connection_manager.get_connection(account_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        # Disconnect if currently connected
        if conn.connected:
            await conn.disconnect()
        
        # Attempt to reconnect
        success = await conn.connect()
        
        if success:
            # Re-register callbacks if needed
            conn.register_callback("position", handle_position_update)
            conn.register_callback("order", handle_order_update)
            conn.register_callback("trade", handle_trade_update)
            conn.register_callback("account", handle_account_update)
            conn.register_callback("quote", handle_quote_update)
            
            # Broadcast connection status update
            await broadcast_update(account_id, "connection_status", {"connected": True})
            
            return {
                "status": "success",
                "message": "Account reconnected successfully",
                "connected": True
            }
        else:
            error_msg = conn.last_error or "Connection failed"
            await broadcast_update(account_id, "connection_status", {"connected": False, "error": error_msg})
            
            return {
                "status": "error",
                "message": f"Failed to reconnect: {error_msg}",
                "connected": False,
                "error": error_msg
            }
    except Exception as e:
        logger.error(f"Reconnect error for {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# WebSocket endpoint for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await websocket.accept()
    websocket_connections.append(websocket)
    
    try:
        # Send initial data
        for account_id in account_data.keys():
            await websocket.send_json({
                "type": "initial_data",
                "account_id": account_id,
                "data": account_data[account_id]
            })
        
        # Keep connection alive
        while True:
            try:
                data = await websocket.receive_text()
                # Handle client messages if needed
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

