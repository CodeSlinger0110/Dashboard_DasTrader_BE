"""
DasTrader Connection Manager
Handles socket connections to DasTrader CMD API
"""
import socket
import asyncio
from typing import Optional, Dict, Callable, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class DasConnection:
    """Manages a single DasTrader connection"""
    
    def __init__(self, account_id: str, host: str, port: int, user: str, password: str, account: str):
        self.account_id = account_id
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.account = account
        self.socket: Optional[socket.socket] = None
        self.connected = False
        self.last_error: Optional[str] = None
        self.data_callbacks: Dict[str, Callable[[str, str], Any]] = {}
        self.reader_task: Optional[asyncio.Task] = None
        
    async def connect(self) -> bool:
        """Connect to DasTrader server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5)
            self.socket.connect((self.host, self.port))
            await asyncio.sleep(0.1)
            
            # Login
            login_data = f"LOGIN {self.user} {self.password} {self.account}\r\n"
            self.socket.sendall(login_data.encode("ascii"))
            await asyncio.sleep(0.1)
            
            # Read login response
            response = self.recvall()
            logger.info(f"Login response for {self.account_id}: {response[:200]}")
            
            self.connected = True
            self.last_error = None
            
            # Start background reader
            self.reader_task = asyncio.create_task(self._background_reader())
            
            return True
        except Exception as e:
            logger.error(f"Connection error for {self.account_id}: {e}")
            self.connected = False
            self.last_error = str(e)
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            return False
    
    def recvall(self) -> str:
        """Receive all available data"""
        if not self.socket:
            return ""
        data = b''
        bufsize = 4096
        try:
            while True:
                packet = self.socket.recv(bufsize)
                if not packet:
                    break
                data += packet
                if len(packet) < bufsize:
                    break
        except socket.timeout:
            pass
        except Exception as e:
            logger.error(f"Recv error: {e}")
        return data.decode("ascii", errors="ignore").strip()
    
    async def send_command(self, command: str) -> str:
        """Send a command and return response"""
        if not self.connected or not self.socket:
            raise Exception("Not connected")
        
        try:
            script = bytearray(command + "\r\n", encoding="ascii")
            self.socket.sendall(script)
            
            # Determine sleep time based on command
            if command.startswith("GET") or command.startswith("NEWORDER") or command.startswith("SL"):
                await asyncio.sleep(0.1)
            elif "REPLACE" in command or "COMPLEXORDER" in command:
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0.0005)
            
            return self.recvall()
        except Exception as e:
            logger.error(f"Send command error: {e}")
            self.connected = False
            raise
    
    async def _background_reader(self):
        """Background task to continuously read data"""
        while self.connected and self.socket:
            try:
                data = self.recvall()
                if data:
                    await self._process_incoming_data(data)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Background reader error: {e}")
                await asyncio.sleep(1)
    
    async def _process_incoming_data(self, data: str):
        """Process incoming data and trigger callbacks"""
        lines = data.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check for position updates
            if line.startswith("%POS"):
                if "position" in self.data_callbacks:
                    await self.data_callbacks["position"](self.account_id, line)
            
            # Check for order updates
            elif line.startswith("%ORDER") or line.startswith("%OrderAct"):
                if "order" in self.data_callbacks:
                    await self.data_callbacks["order"](self.account_id, line)
            
            # Check for trade updates
            elif line.startswith("%TRADE"):
                if "trade" in self.data_callbacks:
                    await self.data_callbacks["trade"](self.account_id, line)
            
            # Check for account info
            elif line.startswith("$AccountInfo") or line.startswith("BP"):
                if "account" in self.data_callbacks:
                    await self.data_callbacks["account"](self.account_id, line)
            
            # Check for quote updates
            elif line.startswith("$Quote"):
                if "quote" in self.data_callbacks:
                    await self.data_callbacks["quote"](self.account_id, line)
    
    def register_callback(self, data_type: str, callback: Callable):
        """Register a callback for data updates"""
        self.data_callbacks[data_type] = callback
    
    async def disconnect(self):
        """Disconnect from server"""
        self.connected = False
        if self.reader_task:
            self.reader_task.cancel()
            try:
                await self.reader_task
            except:
                pass
        
        if self.socket:
            try:
                self.socket.sendall(b'QUIT\r\n')
                self.socket.close()
            except:
                pass
            self.socket = None


class ConnectionManager:
    """Manages multiple DasTrader connections"""
    
    def __init__(self):
        self.connections: Dict[str, DasConnection] = {}
    
    def add_connection(self, account_id: str, host: str, port: int, user: str, password: str, account: str) -> DasConnection:
        """Add a new connection"""
        conn = DasConnection(account_id, host, port, user, password, account)
        self.connections[account_id] = conn
        return conn
    
    async def connect_all(self):
        """Connect to all enabled accounts"""
        results = {}
        for account_id, conn in self.connections.items():
            results[account_id] = await conn.connect()
        return results
    
    async def disconnect_all(self):
        """Disconnect all connections"""
        for conn in self.connections.values():
            await conn.disconnect()
    
    def get_connection(self, account_id: str) -> Optional[DasConnection]:
        """Get a connection by account ID"""
        return self.connections.get(account_id)
    
    def get_all_connections(self) -> Dict[str, DasConnection]:
        """Get all connections"""
        return self.connections

