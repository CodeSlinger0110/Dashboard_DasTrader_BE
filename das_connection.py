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
        self.command_lock = asyncio.Lock()  # Lock to prevent interference between commands and background reader
        self.reader_paused = False  # Flag to pause background reader during commands
        
    async def connect(self) -> bool:
        """Connect to DasTrader server with fast timeout"""
        try:
            # Use asyncio to make socket operations non-blocking
            loop = asyncio.get_event_loop()
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setblocking(False)  # Make non-blocking
            
            # Connect with short timeout (1 second) to fail fast
            try:
                await asyncio.wait_for(
                    loop.sock_connect(self.socket, (self.host, self.port)),
                    timeout=1.0  # 1 second timeout for connection
                )
            except asyncio.TimeoutError:
                logger.warning(f"Connection timeout for {self.account_id} ({self.host}:{self.port}) - DasTrader may not be running")
                self.connected = False
                self.last_error = f"Connection timeout - DasTrader may not be running on {self.host}:{self.port}"
                if self.socket:
                    try:
                        self.socket.close()
                    except:
                        pass
                    self.socket = None
                return False
            except OSError as e:
                # Handle connection refused, network unreachable, etc.
                error_msg = f"Connection failed: {e}"
                logger.warning(f"Connection failed for {self.account_id} ({self.host}:{self.port}) - {error_msg}")
                self.connected = False
                self.last_error = error_msg
                if self.socket:
                    try:
                        self.socket.close()
                    except:
                        pass
                    self.socket = None
                return False
            
            await asyncio.sleep(0.05)
            
            # Login with timeout
            try:
                login_data = f"LOGIN {self.user} {self.password} {self.account}\r\n"
                await asyncio.wait_for(
                    loop.sock_sendall(self.socket, login_data.encode("ascii")),
                    timeout=0.5  # 500ms timeout for login send
                )
                await asyncio.sleep(0.05)
                
                # Read login response with timeout
                response = await asyncio.wait_for(
                    self.recvall_async(timeout=0.5),
                    timeout=0.5  # 500ms timeout for login response
                )
                logger.info(f"Login response for {self.account_id}: {response[:200]}")
            except asyncio.TimeoutError:
                logger.warning(f"Login timeout for {self.account_id} - server may be slow or unresponsive")
                self.connected = False
                self.last_error = "Login timeout"
                if self.socket:
                    try:
                        self.socket.close()
                    except:
                        pass
                    self.socket = None
                return False
            
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
        """Receive all available data (synchronous, for backward compatibility)"""
        if not self.socket:
            return ""
        data = b''
        bufsize = 4096
        try:
            # Temporarily make blocking for recv
            was_blocking = self.socket.getblocking()
            self.socket.setblocking(True)
            self.socket.settimeout(0.5)  # Short timeout
            
            while True:
                try:
                    packet = self.socket.recv(bufsize)
                    if not packet:
                        break
                    data += packet
                    if len(packet) < bufsize:
                        break
                except socket.timeout:
                    break
            self.socket.setblocking(was_blocking)
        except socket.timeout:
            pass
        except Exception as e:
            logger.error(f"Recv error: {e}")
        return data.decode("ascii", errors="ignore").strip()
    
    async def recvall_async(self, timeout: float = 1.0) -> str:
        """Receive all available data asynchronously"""
        if not self.socket:
            return ""
        data = b''
        bufsize = 4096
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    # Use asyncio.wait_for to add timeout
                    packet = await asyncio.wait_for(
                        loop.sock_recv(self.socket, bufsize),
                        timeout=timeout
                    )
                    if not packet:
                        break
                    data += packet
                    if len(packet) < bufsize:
                        break
                except asyncio.TimeoutError:
                    break
        except Exception as e:
            logger.error(f"Async recv error: {e}")
        return data.decode("ascii", errors="ignore").strip()
    
    async def send_command(self, command: str) -> str:
        """Send a command and return response"""
        if not self.connected or not self.socket:
            raise Exception("Not connected")
        
        async with self.command_lock:  # Lock to prevent interference
            try:
                # Pause background reader temporarily
                self.reader_paused = True
                
                loop = asyncio.get_event_loop()
                script = bytearray(command + "\r\n", encoding="ascii")
                await loop.sock_sendall(self.socket, script)
                
                # Determine sleep time based on command (reduced delays)
                if command.startswith("GET") or command.startswith("NEWORDER") or command.startswith("SL"):
                    await asyncio.sleep(0.05)  # Reduced from 0.1
                elif "REPLACE" in command or "COMPLEXORDER" in command:
                    await asyncio.sleep(0.1)  # Reduced from 0.2
                else:
                    await asyncio.sleep(0.01)  # Increased from 0.0005 for reliability
                
                # Use async recv with shorter timeout
                response = await self.recvall_async(timeout=0.5)
                
                return response
            except Exception as e:
                logger.error(f"Send command error: {e}")
                self.connected = False
                raise
            finally:
                # Resume background reader
                self.reader_paused = False
    
    async def _background_reader(self):
        """Background task to continuously read data"""
        loop = asyncio.get_event_loop()
        while self.connected and self.socket:
            try:
                # Skip if a command is being executed
                if self.reader_paused:
                    await asyncio.sleep(0.05)
                    continue
                
                # Try to read data asynchronously
                try:
                    data = await asyncio.wait_for(
                        loop.sock_recv(self.socket, 4096),
                        timeout=0.1
                    )
                    if data:
                        decoded = data.decode("ascii", errors="ignore").strip()
                        if decoded:
                            await self._process_incoming_data(decoded)
                except asyncio.TimeoutError:
                    pass  # No data available, continue
                except Exception as e:
                    logger.error(f"Background reader recv error: {e}")
                
                await asyncio.sleep(0.05)  # Reduced from 0.1
            except Exception as e:
                logger.error(f"Background reader error: {e}")
                await asyncio.sleep(0.5)  # Reduced from 1
    
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
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.sock_sendall(self.socket, b'QUIT\r\n')
                else:
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
        """Connect to all enabled accounts in parallel with fast failure"""
        # Connect to all accounts in parallel for faster startup
        async def connect_account(account_id: str, conn: DasConnection):
            try:
                result = await conn.connect()
                return account_id, result
            except Exception as e:
                logger.error(f"Unexpected error connecting {account_id}: {e}")
                return account_id, False
        
        # Create tasks for all connections
        tasks = [
            connect_account(account_id, conn)
            for account_id, conn in self.connections.items()
        ]
        
        # Wait for all connections with a reasonable timeout (5 seconds total)
        try:
            results_list = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0  # Maximum 5 seconds for all connections
            )
        except asyncio.TimeoutError:
            logger.warning("Connection timeout - some accounts may still be connecting")
            results_list = []
        
        # Convert to dictionary
        results = {}
        for item in results_list:
            if isinstance(item, Exception):
                logger.error(f"Connection task error: {item}")
                continue
            if isinstance(item, tuple) and len(item) == 2:
                account_id, success = item
                results[account_id] = success
        
        # Log summary
        successful = sum(1 for v in results.values() if v)
        failed = len(results) - successful
        logger.info(f"Connection summary: {successful} successful, {failed} failed")
        
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

