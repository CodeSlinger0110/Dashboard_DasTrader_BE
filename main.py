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
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from twilio.rest import Client as TwilioClient

try:
    from .config import (
        ACCOUNTS, ACCOUNTS_DICT, USERS, AUTH_CREDENTIALS, 
        JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRATION_HOURS,
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, TWILIO_WHATSAPP_TO, TWILIO_CONTENT_SID
    )
    from .das_connection import ConnectionManager, DasConnection
    from .data_parser import DataParser
except ImportError:
    from config import (
        ACCOUNTS, ACCOUNTS_DICT, USERS, AUTH_CREDENTIALS, 
        JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRATION_HOURS,
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, TWILIO_WHATSAPP_TO, TWILIO_CONTENT_SID
    )
    from das_connection import ConnectionManager, DasConnection
    from data_parser import DataParser

# Setup logging with detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Set uvicorn access logger to show more details
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.setLevel(logging.INFO)

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
    
    # Fetch initial data for all connected accounts (one-time fetch on startup)
    logger.info("Fetching initial data for all connected accounts...")
    initial_tasks = []
    for account_id, conn in connection_manager.get_all_connections().items():
        if conn.connected:
            initial_tasks.append(update_account_data(account_id, conn))
    
    if initial_tasks:
        await asyncio.gather(*initial_tasks, return_exceptions=True)
        logger.info("Initial data fetch completed")
    
    # Note: Periodic updates disabled - data is refreshed manually via refresh button
    # Real-time updates still work via WebSocket callbacks from DasTrader
    
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
        trades = account_data[account_id]["trades"]
        # Check if trade already exists (by trade_id)
        trade_id = trade.get("trade_id")
        if trade_id:
            # Remove existing trade with same ID to avoid duplicates
            trades = [t for t in trades if t.get("trade_id") != trade_id]
        
        # Add new trade to beginning
        trades.insert(0, trade)
        
        # Keep only last 1000 trades
        if len(trades) > 1000:
            trades = trades[:1000]
        
        account_data[account_id]["trades"] = trades
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
        logger.warning(f"[{account_id}] Skipping update - not connected (host: {conn.host}:{conn.port})")
        return
    
    logger.debug(f"[{account_id}] Starting data update (host: {conn.host}:{conn.port})")
    try:
        # Send commands sequentially to avoid response mixing
        # DasTrader CMD API can mix responses when commands are sent in parallel
        logger.debug(f"[{account_id}] Sending commands sequentially to avoid response mixing")
        
        # GET POSITIONS
        logger.debug(f"[{account_id}] Sending GET POSITIONS...")
        try:
            pos_data = await conn.send_command("GET POSITIONS")
            # For slow connections (different IP), wait longer to ensure response is complete
            delay = 0.3 if conn.host != "127.0.0.1" and conn.host != "localhost" else 0.2
            await asyncio.sleep(delay)
            # Validate response contains position data
            if pos_data:
                if "%POS" in pos_data or "#POS" in pos_data or pos_data.strip().startswith("#POS"):
                    logger.debug(f"[{account_id}] GET POSITIONS response validated ({len(pos_data)} chars)")
                else:
                    logger.error(f"[{account_id}] GET POSITIONS returned wrong data! Expected positions, got: {pos_data[:300]}")
                    # Try to identify what we actually got
                    if "%ORDER" in pos_data or "#Order" in pos_data:
                        logger.error(f"[{account_id}] Got ORDERS data instead of POSITIONS! Response mixing detected.")
                    elif "%TRADE" in pos_data or "#Trade" in pos_data:
                        logger.error(f"[{account_id}] Got TRADES data instead of POSITIONS! Response mixing detected.")
                    pos_data = None  # Don't process wrong data
        except Exception as e:
            logger.error(f"[{account_id}] Error fetching positions: {e}", exc_info=True)
            pos_data = None
        
        # GET ORDERS
        logger.debug(f"[{account_id}] Sending GET ORDERS...")
        try:
            order_data = await conn.send_command("GET ORDERS")
            # For slow connections (different IP), wait longer to ensure response is complete
            delay = 0.3 if conn.host != "127.0.0.1" and conn.host != "localhost" else 0.2
            await asyncio.sleep(delay)
            # Validate response contains order data
            if order_data:
                if "%ORDER" in order_data or "#Order" in order_data or order_data.strip().startswith("#Order"):
                    logger.debug(f"[{account_id}] GET ORDERS response validated ({len(order_data)} chars)")
                else:
                    logger.error(f"[{account_id}] GET ORDERS returned wrong data! Expected orders, got: {order_data[:300]}")
                    # Try to identify what we actually got
                    if "%POS" in order_data or "#POS" in order_data:
                        logger.error(f"[{account_id}] Got POSITIONS data instead of ORDERS! Response mixing detected.")
                    elif "%TRADE" in order_data or "#Trade" in order_data:
                        logger.error(f"[{account_id}] Got TRADES data instead of ORDERS! Response mixing detected.")
                    order_data = None  # Don't process wrong data
        except Exception as e:
            logger.error(f"[{account_id}] Error fetching orders: {e}", exc_info=True)
            order_data = None
        
        # GET TRADES
        logger.debug(f"[{account_id}] Sending GET TRADES...")
        try:
            trade_data = await conn.send_command("GET TRADES")
            # For slow connections (different IP), wait longer to ensure response is complete
            delay = 0.3 if conn.host != "127.0.0.1" and conn.host != "localhost" else 0.2
            await asyncio.sleep(delay)
            # Validate response contains trade data
            if trade_data:
                if "%TRADE" in trade_data or "#Trade" in trade_data or trade_data.strip().startswith("#Trade"):
                    logger.debug(f"[{account_id}] GET TRADES response validated ({len(trade_data)} chars)")
                else:
                    logger.error(f"[{account_id}] GET TRADES returned wrong data! Expected trades, got: {trade_data[:300]}")
                    # Try to identify what we actually got
                    if "%POS" in trade_data or "#POS" in trade_data:
                        logger.error(f"[{account_id}] Got POSITIONS data instead of TRADES! Response mixing detected.")
                    elif "%ORDER" in trade_data or "#Order" in trade_data:
                        logger.error(f"[{account_id}] Got ORDERS data instead of TRADES! Response mixing detected.")
                    trade_data = None  # Don't process wrong data
        except Exception as e:
            logger.error(f"[{account_id}] Error fetching trades: {e}", exc_info=True)
            trade_data = None
        
        # GET AccountInfo
        logger.debug(f"[{account_id}] Sending GET AccountInfo...")
        try:
            acc_data = await conn.send_command("GET AccountInfo")
            # For slow connections (different IP), wait longer to ensure response is complete
            delay = 0.3 if conn.host != "127.0.0.1" and conn.host != "localhost" else 0.1
            await asyncio.sleep(delay)
            logger.debug(f"[{account_id}] GET AccountInfo response: {acc_data[:200] if acc_data else 'None'}")
        except Exception as e:
            logger.error(f"[{account_id}] Error fetching account info: {e}", exc_info=True)
            acc_data = None
        
        # GET BP
        logger.debug(f"[{account_id}] Sending GET BP...")
        try:
            bp_data = await conn.send_command("GET BP")
            logger.debug(f"[{account_id}] GET BP response: {bp_data[:200] if bp_data else 'None'}")
        except Exception as e:
            logger.error(f"[{account_id}] Error fetching buying power: {e}", exc_info=True)
            bp_data = None
        
        logger.debug(f"[{account_id}] All commands completed. Results: pos={type(pos_data).__name__ if pos_data else 'None'}, order={type(order_data).__name__ if order_data else 'None'}, trade={type(trade_data).__name__ if trade_data else 'None'}")
        
        # Process results (validation already done above)
        if account_id in account_data:
            # Process positions (only if validated above)
            if pos_data and not isinstance(pos_data, Exception):
                logger.info(f"[{account_id}] Received positions data: {len(pos_data)} chars, preview: {pos_data[:200]}")
                positions = data_parser.parse_positions(pos_data)
                account_data[account_id]["positions"] = positions
                logger.info(f"[{account_id}] Parsed {len(positions)} positions")
            elif isinstance(pos_data, Exception):
                logger.error(f"[{account_id}] Error fetching positions from {conn.host}:{conn.port}: {pos_data}")
            elif not pos_data:
                logger.warning(f"[{account_id}] Empty or invalid positions response from {conn.host}:{conn.port}")
            
            # Process orders (only if validated above)
            if order_data and not isinstance(order_data, Exception):
                logger.info(f"[{account_id}] Received orders data: {len(order_data)} chars, preview: {order_data[:200]}")
                orders = data_parser.parse_orders(order_data)
                account_data[account_id]["orders"] = orders
                logger.info(f"[{account_id}] Parsed {len(orders)} orders")
            elif isinstance(order_data, Exception):
                logger.error(f"[{account_id}] Error fetching orders from {conn.host}:{conn.port}: {order_data}")
            elif not order_data:
                logger.warning(f"[{account_id}] Empty or invalid orders response from {conn.host}:{conn.port}")
            
            # Process trades (only if validated above)
            if trade_data and not isinstance(trade_data, Exception):
                logger.info(f"[{account_id}] Received trades data: {len(trade_data)} chars, preview: {trade_data[:200]}")
                new_trades = data_parser.parse_trades(trade_data)
                
                # Merge with existing trades and deduplicate by trade_id
                existing_trades = account_data[account_id].get("trades", [])
                existing_trade_ids = {
                    t.get("trade_id"): t 
                    for t in existing_trades 
                    if isinstance(t, dict) and t.get("trade_id")
                }
                
                # Add new trades, updating existing ones
                for trade in new_trades:
                    if isinstance(trade, dict):
                        trade_id = trade.get("trade_id")
                        if trade_id:
                            existing_trade_ids[trade_id] = trade
                
                # Convert back to list, sorted by time (most recent first)
                merged_trades = list(existing_trade_ids.values())
                # Sort by time if available, otherwise keep order
                try:
                    merged_trades.sort(key=lambda t: t.get("time", ""), reverse=True)
                except:
                    pass
                
                # Keep only last 1000 trades
                if len(merged_trades) > 1000:
                    merged_trades = merged_trades[:1000]
                
                account_data[account_id]["trades"] = merged_trades
                logger.info(f"[{account_id}] Parsed {len(new_trades)} new trades, total {len(merged_trades)} trades (deduplicated)")
            elif isinstance(trade_data, Exception):
                logger.error(f"[{account_id}] Error fetching trades from {conn.host}:{conn.port}: {trade_data}")
            elif not trade_data:
                logger.warning(f"[{account_id}] Empty or invalid trades response from {conn.host}:{conn.port}")
            
            if acc_data and not isinstance(acc_data, Exception):
                logger.info(f"[{account_id}] Received account info data: {len(acc_data)} chars, preview: {acc_data[:200]}")
                info = data_parser.parse_account_info(acc_data)
                if info:
                    account_data[account_id]["account_info"] = info
                    logger.info(f"[{account_id}] Account info updated: {info}")
                else:
                    logger.warning(f"[{account_id}] Failed to parse account info from: {acc_data[:200]}")
            elif isinstance(acc_data, Exception):
                logger.error(f"[{account_id}] Error fetching account info from {conn.host}:{conn.port}: {acc_data}")
            elif not acc_data:
                logger.warning(f"[{account_id}] Empty account info response from {conn.host}:{conn.port}")
            
            if bp_data and not isinstance(bp_data, Exception):
                logger.info(f"[{account_id}] Received buying power data: {len(bp_data)} chars, preview: {bp_data[:200]}")
                bp = data_parser.parse_buying_power(bp_data)
                if bp:
                    account_data[account_id]["buying_power"] = bp
                    logger.info(f"[{account_id}] Buying power updated: {bp}")
                else:
                    logger.warning(f"[{account_id}] Failed to parse buying power from: {bp_data[:200]}")
            elif isinstance(bp_data, Exception):
                logger.error(f"[{account_id}] Error fetching buying power from {conn.host}:{conn.port}: {bp_data}")
            elif not bp_data:
                logger.warning(f"[{account_id}] Empty buying power response from {conn.host}:{conn.port}")
            
            account_data[account_id]["last_update"] = datetime.now().isoformat()
            logger.debug(f"[{account_id}] Data update completed successfully")
    except Exception as e:
        logger.error(f"[{account_id}] Error updating data from {conn.host}:{conn.port}: {e}", exc_info=True)


# Periodic updates disabled - data is refreshed manually via refresh button
# Real-time updates still work via WebSocket callbacks from DasTrader
# async def periodic_updates():
#     """Periodic updates for positions, orders, account info"""
#     logger.info("Starting periodic updates task")
#     while True:
#         try:
#             # Update all accounts in parallel for better performance
#             update_tasks = []
#             connected_accounts = []
#             for account_id, conn in connection_manager.get_all_connections().items():
#                 if conn.connected:
#                     connected_accounts.append(f"{account_id}({conn.host}:{conn.port})")
#                     update_tasks.append(update_account_data(account_id, conn))
#                 else:
#                     logger.debug(f"[{account_id}] Skipping update - not connected to {conn.host}:{conn.port}")
#             
#             if update_tasks:
#                 logger.debug(f"Updating {len(update_tasks)} connected accounts: {', '.join(connected_accounts)}")
#                 await asyncio.gather(*update_tasks, return_exceptions=True)
#                 logger.debug("Periodic update cycle completed")
#             else:
#                 logger.warning("No connected accounts to update")
#             
#             await asyncio.sleep(5)  # Update every 5 seconds
#         except Exception as e:
#             logger.error(f"Error in periodic updates: {e}", exc_info=True)
#             await asyncio.sleep(5)


# REST API Endpoints

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "DasTrader Dashboard API", "status": "running"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "websocket_endpoint": "/ws",
        "connected_accounts": len([acc for acc in account_data.keys()]),
        "active_websocket_connections": len(websocket_connections)
    }

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

# Webhook endpoint for DAS signals (public, no authentication required)
class DasSignalRequest(BaseModel):
    source: str
    symbol: str
    price: str
    shares: str
    alert: str

def send_whatsapp_message(message: str = "", use_template: bool = False, template_variables: dict = None) -> bool:
    """
    Send WhatsApp message via Twilio to multiple recipients
    
    Args:
        message: Plain text message (used if use_template=False)
        use_template: If True, use content template instead of plain text
        template_variables: Variables for content template (dict or JSON string)
    
    Returns:
        True if at least one message was sent successfully, False otherwise
    """
    try:
        # Get Twilio credentials from environment variables or config
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID)
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN)
        from_number = os.getenv("TWILIO_WHATSAPP_FROM", TWILIO_WHATSAPP_FROM)
        to_numbers_env = os.getenv("TWILIO_WHATSAPP_TO", None)
        to_numbers_config = TWILIO_WHATSAPP_TO
        content_sid = os.getenv("TWILIO_CONTENT_SID", TWILIO_CONTENT_SID)
        
        # Check if Twilio is configured
        if not account_sid or not auth_token:
            logger.warning("Twilio not configured. Please set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.")
            return False
        
        # Parse recipient numbers - handle both string and list formats
        if to_numbers_env:
            # Environment variable takes precedence
            # Try to parse as JSON array first, then as comma-separated string
            try:
                to_numbers = json.loads(to_numbers_env)
                if isinstance(to_numbers, str):
                    to_numbers = [to_numbers]
            except (json.JSONDecodeError, TypeError):
                # If not JSON, treat as comma-separated string
                to_numbers = [num.strip() for num in to_numbers_env.split(",")]
        else:
            # Use config value
            if isinstance(to_numbers_config, str):
                to_numbers = [to_numbers_config]
            elif isinstance(to_numbers_config, list):
                to_numbers = to_numbers_config
            else:
                logger.error(f"Invalid TWILIO_WHATSAPP_TO format: {type(to_numbers_config)}")
                return False
        
        # Ensure we have at least one recipient
        if not to_numbers or len(to_numbers) == 0:
            logger.warning("No WhatsApp recipients configured.")
            return False
        
        # Initialize Twilio client
        client = TwilioClient(account_sid, auth_token)
        
        # Track success
        success_count = 0
        failed_count = 0
        
        # Send WhatsApp message to each recipient
        for to_number in to_numbers:
            try:
                if use_template and content_sid:
                    # Use content template
                    content_vars = template_variables if template_variables else {}
                    if isinstance(content_vars, dict):
                        content_vars = json.dumps(content_vars)
                    
                    message_obj = client.messages.create(
                        from_=from_number,
                        content_sid=content_sid,
                        content_variables=content_vars,
                        to=to_number
                    )
                    logger.info(f"WhatsApp template message sent to {to_number} successfully. SID: {message_obj.sid}")
                    success_count += 1
                else:
                    # Send plain text message
                    message_obj = client.messages.create(
                        body=message,
                        from_=from_number,
                        to=to_number
                    )
                    logger.info(f"WhatsApp text message sent to {to_number} successfully. SID: {message_obj.sid}")
                    success_count += 1
            except Exception as e:
                logger.error(f"Error sending WhatsApp message to {to_number}: {e}", exc_info=True)
                failed_count += 1
        
        # Return True if at least one message was sent successfully
        if success_count > 0:
            logger.info(f"WhatsApp messages sent: {success_count} successful, {failed_count} failed")
            return True
        else:
            logger.error(f"Failed to send WhatsApp messages to all recipients ({failed_count} failed)")
            return False
            
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}", exc_info=True)
        return False

@app.post("/webhook/das")
async def receive_das_signal(signal: DasSignalRequest):
    """
    Public webhook endpoint to receive DAS trading signals
    No authentication required - this is called by external scripts
    """
    try:
        logger.info(f"Received DAS signal: {signal.symbol} @ {signal.price}, {signal.shares} shares, alert: {signal.alert}")
        
        # Format WhatsApp message (plain text)
        message = f"🚨 DAS Trading Alert 🚨\n\n"
        message += f"Symbol: {signal.symbol}\n"
        message += f"Price: ${signal.price}\n"
        message += f"Shares: {signal.shares}\n"
        message += f"Alert Type: {signal.alert}\n"
        message += f"Source: {signal.source}\n"
        message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        success = send_whatsapp_message(message)
        
        if success:
            return {
                "status": "success",
                "message": "Signal received and WhatsApp notification sent",
                "data": {
                    "symbol": signal.symbol,
                    "price": signal.price,
                    "shares": signal.shares,
                    "alert": signal.alert
                }
            }
        else:
            return {
                "status": "partial_success",
                "message": "Signal received but WhatsApp notification failed",
                "data": {
                    "symbol": signal.symbol,
                    "price": signal.price,
                    "shares": signal.shares,
                    "alert": signal.alert
                }
            }
    except Exception as e:
        logger.error(f"Error processing DAS signal: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing signal: {str(e)}"
        )

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
    
    # Get positions safely - handle None or empty list
    positions_raw = account_data[account_id].get("positions")
    
    if not positions_raw or not isinstance(positions_raw, list):
        return {"account_id": account_id, "positions": []}
    
    # Filter out positions with zero quantity (closed positions)
    positions = [pos.copy() for pos in positions_raw if isinstance(pos, dict) and pos.get("quantity", 0) != 0]
    
    quotes = account_data[account_id].get("quotes", {})
    
    # Calculate current mark price and unrealized PnL from quotes
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        symbol = pos.get("symbol")
        if symbol and symbol in quotes:
            quote = quotes[symbol]
            if isinstance(quote, dict):
                mark_price = quote.get("l", pos.get("avg_cost", 0))
                pos["mark_price"] = mark_price
                # Recalculate unrealized PnL
                quantity = pos.get("quantity", 0)
                avg_cost = pos.get("avg_cost", 0)
                if pos.get("type") == "short":
                    pos["unrealized_pnl"] = (avg_cost - mark_price) * quantity
                else:
                    pos["unrealized_pnl"] = (mark_price - avg_cost) * quantity
    
    return {"account_id": account_id, "positions": positions}


@app.get("/api/accounts/{account_id}/orders")
async def get_orders(account_id: str, current_user: str = Depends(verify_token)):
    """Get orders for an account"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Get orders safely - handle None or empty list
    orders_raw = account_data[account_id].get("orders")
    
    if not orders_raw or not isinstance(orders_raw, list):
        return {"account_id": account_id, "orders": []}
    
    # Filter out invalid orders and ensure they're dictionaries
    orders = [order for order in orders_raw if isinstance(order, dict)]
    
    return {"account_id": account_id, "orders": orders}


@app.get("/api/accounts/{account_id}/trades")
async def get_trades(account_id: str, limit: int = 100, current_user: str = Depends(verify_token)):
    """Get recent trades for an account"""
    logger.info(f"GET /api/accounts/{account_id}/trades")
    if account_id not in account_data:
        logger.warning(f"Account {account_id} not found in account_data")
        raise HTTPException(status_code=404, detail="Account not found")
    
    trades_raw = account_data[account_id].get("trades")
    
    if not trades_raw or not isinstance(trades_raw, list):
        logger.info(f"[{account_id}] No trades data available, returning empty list")
        return {"account_id": account_id, "trades": []}
    
    # Deduplicate trades by trade_id
    seen_trade_ids = set()
    unique_trades = []
    for trade in trades_raw:
        if isinstance(trade, dict):
            trade_id = trade.get("trade_id")
            if trade_id and trade_id in seen_trade_ids:
                continue  # Skip duplicate
            if trade_id:
                seen_trade_ids.add(trade_id)
            unique_trades.append(trade)
    
    # Sort by time (most recent first) and limit
    try:
        unique_trades.sort(key=lambda t: t.get("time", ""), reverse=True)
    except:
        pass
    
    trades = unique_trades[:limit]
    logger.info(f"[{account_id}] Returning {len(trades)} trades (deduplicated from {len(trades_raw)} total)")
    return {"account_id": account_id, "trades": trades}


@app.get("/api/accounts/{account_id}/overview")
async def get_account_overview(account_id: str, current_user: str = Depends(verify_token)):
    """Get account overview (equity, margin, cash, etc.)"""
    logger.info(f"GET /api/accounts/{account_id}/overview")
    if account_id not in account_data:
        logger.warning(f"Account {account_id} not found in account_data")
        raise HTTPException(status_code=404, detail="Account not found")
    
    data = account_data[account_id]
    # Handle None values - if account_info or buying_power is None, use empty dict
    account_info = data.get("account_info") or {}
    buying_power = data.get("buying_power") or {}
    positions_raw = data.get("positions", [])
    
    # Filter positions with quantity > 0 and ensure they're dictionaries
    positions = [p for p in positions_raw if isinstance(p, dict) and p.get("quantity", 0) != 0]
    
    # Get quotes for mark price calculation
    quotes = data.get("quotes", {})
    
    # Calculate unrealized PnL for each position and total
    total_unrealized = 0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        symbol = pos.get("symbol")
        quantity = pos.get("quantity", 0)
        avg_cost = pos.get("avg_cost", 0)
        
        # Get mark price from quote or use avg_cost
        mark_price = pos.get("mark_price") or avg_cost
        if symbol and symbol in quotes:
            quote = quotes[symbol]
            if isinstance(quote, dict):
                mark_price = quote.get("l", mark_price)  # Use last price from quote
        
        # Calculate unrealized PnL
        if pos.get("type") == "short":
            unrealized = (avg_cost - mark_price) * quantity
        else:
            unrealized = (mark_price - avg_cost) * quantity
        
        pos["unrealized_pnl"] = unrealized
        pos["mark_price"] = mark_price
        total_unrealized += unrealized
    
    # Calculate exposure by asset class (simplified - all equities for now)
    equity_exposure = sum(
        abs(p.get("quantity", 0) * (p.get("mark_price") or p.get("avg_cost", 0))) 
        for p in positions 
        if isinstance(p, dict)
    )
    
    # Safely get account info values with defaults
    sec_fee = account_info.get("sec_fee", 0) if isinstance(account_info, dict) else 0
    finra_fee = account_info.get("finra_fee", 0) if isinstance(account_info, dict) else 0
    ecn_fee = account_info.get("ecn_fee", 0) if isinstance(account_info, dict) else 0
    
    # Log overview data for debugging
    logger.debug(f"[{account_id}] Overview - account_info: {account_info}, buying_power: {buying_power}")
    logger.debug(f"[{account_id}] Overview - positions: {len(positions)}, total_unrealized: {total_unrealized}, equity_exposure: {equity_exposure}")
    
    result = {
        "account_id": account_id,
        "user_id": data.get("user_id"),
        "user_name": data.get("user_name"),
        "current_equity": account_info.get("current_equity", 0) if isinstance(account_info, dict) else 0,
        "open_equity": account_info.get("open_equity", 0) if isinstance(account_info, dict) else 0,
        "realized_pl": account_info.get("realized_pl", 0) if isinstance(account_info, dict) else 0,
        "unrealized_pl": total_unrealized,
        "net_pl": account_info.get("net_pl", 0) if isinstance(account_info, dict) else 0,
        "buying_power": buying_power.get("current_bp", 0) if isinstance(buying_power, dict) else 0,
        "overnight_bp": buying_power.get("overnight_bp", 0) if isinstance(buying_power, dict) else 0,
        "equity_exposure": equity_exposure,
        "commission": account_info.get("commission", 0) if isinstance(account_info, dict) else 0,
        "fees": sec_fee + finra_fee + ecn_fee,
        "last_update": data.get("last_update")
    }
    
    logger.info(f"[{account_id}] Returning overview: equity={result['current_equity']}, bp={result['buying_power']}, unrealized_pl={result['unrealized_pl']}")
    return result


@app.get("/api/accounts/{account_id}/activity")
async def get_activity(account_id: str, limit: int = 100, current_user: str = Depends(verify_token)):
    """Get activity log (trades, order actions, etc.)"""
    if account_id not in account_data:
        raise HTTPException(status_code=404, detail="Account not found")
    
    activities = []
    seen_trade_ids = set()  # Track seen trade IDs to avoid duplicates
    
    # Add trades
    trades_raw = account_data[account_id].get("trades")
    
    if trades_raw and isinstance(trades_raw, list):
        for trade in trades_raw:
            if isinstance(trade, dict):
                trade_id = trade.get("trade_id")
                # Skip if we've already seen this trade
                if trade_id and trade_id in seen_trade_ids:
                    continue
                if trade_id:
                    seen_trade_ids.add(trade_id)
                
                activities.append({
                    "type": "trade",
                    "timestamp": trade.get("time", ""),
                    "symbol": trade.get("symbol", ""),
                    "side": trade.get("side", ""),
                    "quantity": trade.get("quantity", 0),
                    "price": trade.get("price", 0),
                    "realized_pl": trade.get("realized_pl", 0),
                    "data": trade
                })
    
    # Sort by timestamp (most recent first)
    activities.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    logger.info(f"[{account_id}] Returning {len(activities[:limit])} activities (deduplicated from {len(activities)} total)")
    return {"account_id": account_id, "activities": activities[:limit]}


@app.post("/api/accounts/{account_id}/refresh")
async def refresh_account_data(account_id: str, current_user: str = Depends(verify_token)):
    """Manually refresh account data"""
    logger.info(f"[{account_id}] Manual refresh requested")
    conn = connection_manager.get_connection(account_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not conn.connected:
        raise HTTPException(status_code=400, detail="Account not connected")
    
    try:
        # Use the same update function to ensure consistency
        await update_account_data(account_id, conn)
        logger.info(f"[{account_id}] Manual refresh completed")
        return {"status": "success", "message": "Data refreshed"}
    except Exception as e:
        logger.error(f"[{account_id}] Error during manual refresh: {e}", exc_info=True)
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
    try:
        # Accept connection with origin check disabled (handled by reverse proxy)
        await websocket.accept()
        client_info = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        logger.info(f"WebSocket connection accepted from {client_info}")
        websocket_connections.append(websocket)
        
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
                logger.info(f"WebSocket disconnected: {websocket.client}")
                break
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected during accept: {websocket.client}")
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)
            logger.info(f"WebSocket removed from connections list")


if __name__ == "__main__":
    import uvicorn
    # Configure uvicorn logging to show all logs
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "access": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout"
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout"
            }
        },
        "loggers": {
            "": {"handlers": ["default"], "level": "INFO"},
            "uvicorn": {"handlers": ["default"], "level": "INFO"},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO"}
        }
    }
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        log_config=log_config,
        log_level="info"
    )

